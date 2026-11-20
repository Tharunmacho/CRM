"""The MD role (stored as `manager`) has every privilege an Admin has; staff (recruiters) are unchanged.

Owner's rule: "the manager role is called MD, and whatever an Admin sees and does, the MD sees and does too."
These tests pin the one rule (`app.core.roles.is_admin_level`), the one label table, the role words the API
accepts, and a matrix over representative admin-only endpoints: Admin and MD pass the role gate, staff are refused.
The matrix runs on a throwaway local Postgres (TEST_DATABASE_URL, a loopback crm_schema_test* database).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core import roles
from app.core.deps import require_admin_level, require_role
from app.core.roles import (ADMIN_LEVEL_ROLES, ROLE_LABELS, ROLE_ORDER, is_admin_level, is_real_admin, parse_role, role_label)
from app.core.security import SESSION_COOKIE, hash_password
from app.db.models import Base, Role, Session as LoginSession, User
from app.db.session import get_db
from test_database_naming import _guard_test_url


# ----------------------------------------------------------------------------------------- the one rule

def test_admin_level_is_admin_and_md_only():
    assert is_admin_level(Role.admin) and is_admin_level(Role.manager)
    assert not is_admin_level(Role.staff)
    assert is_admin_level("admin") and is_admin_level("manager") and is_admin_level("md") and is_admin_level("MD")
    assert not is_admin_level("staff") and not is_admin_level("") and not is_admin_level(None)
    assert is_admin_level(User(role=Role.manager)) and not is_admin_level(User(role=Role.staff))
    assert ADMIN_LEVEL_ROLES == (Role.admin, Role.manager)


def test_only_a_real_admin_counts_as_the_last_admin():
    assert is_real_admin(Role.admin) and is_real_admin(User(role=Role.admin))
    assert not is_real_admin(Role.manager) and not is_real_admin("md") and not is_real_admin(Role.staff)


def test_one_label_table_manager_reads_as_md():
    assert ROLE_LABELS == {"admin": "Admin", "manager": "MD", "staff": "Recruiter"}
    assert role_label(Role.manager) == "MD" and role_label(User(role=Role.manager)) == "MD"
    assert role_label("admin") == "Admin" and role_label("staff") == "Recruiter"
    assert [role.value for role in ROLE_ORDER] == ["admin", "manager", "staff"]   # a role picker offers Admin, MD, Staff
    assert "Manager" not in ROLE_LABELS.values()
    assert roles.ROLE_LABELS is ROLE_LABELS


@pytest.mark.parametrize("word, expected", [("admin", Role.admin), ("manager", Role.manager), ("md", Role.manager), ("MD", Role.manager),
                                            (" Md ", Role.manager), ("staff", Role.staff), (Role.manager, Role.manager)])
def test_api_accepts_md_and_manager_for_the_same_role(word, expected):
    assert parse_role(word) is expected


def test_an_unknown_role_word_is_refused():
    for word in ("owner", "", None, "mdx"):
        with pytest.raises(ValueError):
            parse_role(word)


def test_create_user_body_normalises_md_to_the_stored_manager():
    from app.api.routers.users import CreateUserIn, UpdateUserIn
    base = {"name": "Meera", "email": "meera@example.test", "password": "a-long-password"}
    for word in ("md", "MD", "manager", "Manager"):
        assert CreateUserIn(**base, role=word).role is Role.manager
    assert UpdateUserIn(role="md").role is Role.manager
    assert CreateUserIn(**base).role is Role.staff
    with pytest.raises(ValueError):
        CreateUserIn(**base, role="owner")


def test_require_role_naming_admin_also_lets_an_md_in_and_never_staff():
    from fastapi import HTTPException
    for gate in (require_role(Role.admin), require_admin_level(), require_role(Role.admin, Role.manager)):
        assert gate(user=User(role=Role.admin)).role is Role.admin
        assert gate(user=User(role=Role.manager)).role is Role.manager
        with pytest.raises(HTTPException) as refused:
            gate(user=User(role=Role.staff))
        assert refused.value.status_code == 403
    # Roles that were never named stay refused: this is not "everyone".
    only_staff = require_role(Role.staff)
    with pytest.raises(HTTPException):
        only_staff(user=User(role=Role.manager))


def test_no_user_facing_manager_wording_is_left_in_the_role_words():
    from app.services import daily_work_report
    assert daily_work_report.ROLE_NAMES is ROLE_LABELS
    assert daily_work_report.ROLE_NAMES["manager"] == "MD"


# ----------------------------------------------------------------------------------------- the matrix

ADMIN_ONLY = [
    # agency settings, currencies, categories
    ("PUT", "/api/settings", {}),
    ("POST", "/api/settings/currencies", {}),
    ("PUT", "/api/settings/currencies/ZZZ", {}),
    ("DELETE", "/api/settings/currencies/ZZZ", None),
    ("POST", "/api/settings/categories", {}),
    ("DELETE", "/api/settings/categories/none", None),
    # users management
    ("GET", "/api/users", None),
    ("POST", "/api/users", {}),
    ("PATCH", "/api/users/none", {}),
    ("POST", "/api/users/none/password", {"password": "another-long-password"}),
    ("POST", "/api/users/none/force-logout", None),
    ("GET", "/api/users/none/audit", None),
    # invoices and fees
    ("GET", "/api/candidates/none/invoice", None),
    ("PUT", "/api/candidates/none/invoice", {}),
    ("POST", "/api/candidates/none/invoice/reconcile", None),
    ("GET", "/api/invoice-settings", None),
    ("PUT", "/api/invoice-settings", {}),
    ("GET", "/api/invoices/finance-summary", None),
    ("GET", "/api/candidates/none/invoice/payments", None),
    ("POST", "/api/candidates/none/invoice/payments", {}),
    # remark templates CRUD
    ("POST", "/api/remark-templates", {}),
    ("PATCH", "/api/remark-templates/none", {}),
    ("DELETE", "/api/remark-templates/none", None),
    # lead status templates: anybody signed in adds one, only admin/MD changes or deletes
    ("PATCH", "/api/lead-status-templates/none", {}),
    ("DELETE", "/api/lead-status-templates/none", None),
    # candidate merge / delete / export / assign-waiting
    ("POST", "/api/candidates/a/merge-into/b", {}),
    ("DELETE", "/api/candidates/none", None),
    ("GET", "/api/candidates/export", None),
    ("POST", "/api/candidates/assign-waiting", None),
    # the one-off sweep of the Data Bank
    ("POST", "/api/candidates/resort", {}),
    # reports
    ("GET", "/api/reports/staff-weekly", None),
    ("GET", "/api/reports/staff-weekly/none", None),
    # order / job management
    ("DELETE", "/api/orders/none", None),
    ("DELETE", "/api/companies/none", None),
    ("DELETE", "/api/clients/none", None),
    # workflow settings and the 48-hour timer
    ("PATCH", "/api/workflow/settings", {}),
    ("POST", "/api/tasks/none/reset-sla", {}),
    # daily goals
    ("PUT", "/api/daily-goals/default", {}),
    ("PUT", "/api/daily-goals/staff/none", {}),
    # lead sheet administration
    ("GET", "/api/leads/batches", None),
    ("DELETE", "/api/leads/batches/none", None),
    ("POST", "/api/leads/assign", {}),
    ("DELETE", "/api/leads/none", None),
    # form options and attendance corrections
    ("POST", "/api/form-options", {}),
    ("POST", "/api/form-options/reorder", {}),
    ("DELETE", "/api/form-options/none", None),
]


@pytest.fixture(scope="module")
def matrix_env():
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL to an isolated loopback crm_schema_test* DB")
    engine = create_engine(_guard_test_url(raw), connect_args={"connect_timeout": 5, "options": "-c timezone=UTC"})
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        for identifier, role in (("admin", Role.admin), ("md", Role.manager), ("staff", Role.staff)):
            db.add(User(id=identifier, name=identifier.upper(), email=f"{identifier}@example.test", role=role, active=True,
                        receives_auto_assign=role is Role.staff, on_leave=False, password_hash=hash_password("initial-test-password")))
        db.flush()
        for identifier in ("admin", "md", "staff"):
            db.add(LoginSession(id=f"session-{identifier}", user_id=identifier, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        db.commit()

    from app.api.routers import (candidate_import, candidate_invoices, candidate_merge, candidates, daily_work, data, form_options,
                                 invoice_payments, lead_bulk, lead_status_templates, leads, remark_templates, reports, settings, users, workflow)
    application = FastAPI()
    for module in (settings, users, candidate_invoices, invoice_payments, remark_templates, candidate_merge, candidates, reports,
                   data, workflow, daily_work, leads, lead_bulk, lead_status_templates, form_options):
        application.include_router(module.router)
    application.include_router(candidate_import.candidate_reading_router)

    def database():
        with factory() as db:
            yield db
    application.dependency_overrides[get_db] = database
    try:
        with TestClient(application, base_url="https://testserver", raise_server_exceptions=False) as client:
            client.factory = factory
            yield client
    finally:
        engine.dispose()


def call(client, session, method, path, payload):
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE, f"session-{session}")
    return client.request(method, path, **({"json": payload} if payload is not None else {}))


@pytest.mark.parametrize("method, path, payload", ADMIN_ONLY, ids=[f"{m} {p}" for m, p, _ in ADMIN_ONLY])
def test_admin_and_md_pass_the_gate_and_staff_are_refused(matrix_env, method, path, payload):
    admin = call(matrix_env, "admin", method, path, payload)
    md = call(matrix_env, "md", method, path, payload)
    staff = call(matrix_env, "staff", method, path, payload)
    assert staff.status_code == 403, f"staff reached {method} {path}: {staff.status_code}"
    assert admin.status_code not in (401, 403), f"admin refused {method} {path}: {admin.status_code} {admin.text[:200]}"
    assert md.status_code not in (401, 403), f"MD refused {method} {path}: {md.status_code} {md.text[:200]}"


def test_md_gets_exactly_the_answer_an_admin_gets_on_reads(matrix_env):
    for path in ("/api/users", "/api/invoice-settings", "/api/invoices/finance-summary", "/api/leads/batches", "/api/reports/staff-weekly"):
        admin = call(matrix_env, "admin", "GET", path, None)
        md = call(matrix_env, "md", "GET", path, None)
        assert md.status_code == admin.status_code, path
        if admin.status_code == 200 and path != "/api/reports/staff-weekly":
            assert md.json() == admin.json(), path


def test_users_list_labels_the_stored_value_and_never_the_word_manager(matrix_env):
    listed = call(matrix_env, "md", "GET", "/api/users", None).json()
    assert {row["id"]: row["role"] for row in listed} == {"admin": "admin", "md": "manager", "staff": "staff"}


def test_remark_template_list_all_flag_is_for_admin_level_only(matrix_env):
    from app.db.models import RemarkTemplate
    with matrix_env.factory() as db:
        db.add_all([RemarkTemplate(id="rt-on", body="Waiting for documents", active=True),
                    RemarkTemplate(id="rt-off", body="Retired remark", active=False)])
        db.commit()
    for session, expected in (("admin", {"rt-on", "rt-off"}), ("md", {"rt-on", "rt-off"}), ("staff", {"rt-on"})):
        response = call(matrix_env, session, "GET", "/api/remark-templates?all=1", None)
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == expected, session
