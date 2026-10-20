"""Authentication must use persisted accounts, including after an application restart.

Only the portable authentication tables are created in an isolated SQLite database.
Production settings and the configured database are never used. Startup and readiness
checks are invoked explicitly with the isolated database; other request tests use an
auth-only application. PostgreSQL migrations are outside the scope of these tests.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import SESSION_COOKIE, hash_password, session_expiry, verify_password
from app.db.models import AssignmentPointer, Base, Role, Session as SessionRow, User
from app.services import bootstrap

# Importing the dependency normally loads backend/.env. Supplying an inert settings
# object keeps even that initialization away from a developer's configured database.
with patch(
    "app.core.config.get_settings",
    return_value=SimpleNamespace(
        database_url="postgresql+psycopg://unused:unused@127.0.0.1:1/auth_tests"
    ),
):
    from app import cli
    from app.api.routers import auth
    from app.core.deps import require_role
    from app.db.session import get_db


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[User.__table__, SessionRow.__table__, AssignmentPointer.__table__]
    )
    factory = sessionmaker(bind=engine)

    # PostgreSQL returns aware timestamps; SQLite drops tzinfo. Normalize only this
    # fixture's loaded session rows so requests exercise the same expiry comparison.
    def restore_timezone(row, context, *args):
        for field in ("created_at", "expires_at"):
            value = getattr(row, field)
            if value is not None and value.tzinfo is None:
                setattr(row, field, value.replace(tzinfo=timezone.utc))

    event.listen(SessionRow, "load", restore_timezone)
    event.listen(SessionRow, "refresh", restore_timezone)
    try:
        yield factory
    finally:
        event.remove(SessionRow, "load", restore_timezone)
        event.remove(SessionRow, "refresh", restore_timezone)
        engine.dispose()


@pytest.fixture
def client(database):
    application = FastAPI()
    application.include_router(auth.router)

    def isolated_db():
        with database() as db:
            yield db

    application.dependency_overrides[get_db] = isolated_db

    @application.get("/admin-only")
    def admin_only(user: User = Depends(require_role(Role.admin))):
        return {"id": user.id}

    # HTTPS lets the client send the application's Secure session cookie.
    with TestClient(application, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def password_hash():
    return hash_password("a-test-only-password")


def add_user(database, password_hash, **overrides):
    values = {
        "name": "Persisted account",
        "email": "saved-account@example.test",
        "role": Role.staff,
        "active": True,
        "receives_auto_assign": False,
        "on_leave": True,
        "password_hash": password_hash,
    }
    values.update(overrides)
    with database() as db:
        user = User(**values)
        db.add(user)
        db.commit()
        return user.id


def account_snapshot(database):
    with database() as db:
        return [
            dict(row._mapping)
            for row in db.execute(select(User.__table__).order_by(User.email))
        ]


def add_session(database, user_id, *, expired=False):
    with database() as db:
        row = SessionRow(
            user_id=user_id,
            expires_at=(
                datetime.now(timezone.utc) - timedelta(minutes=1)
                if expired else session_expiry()
            ),
        )
        db.add(row)
        db.commit()
        return row.id


@pytest.fixture
def statements(database):
    recorded = []
    engine = database.kw["bind"]

    def capture(connection, cursor, statement, parameters, context, executemany):
        recorded.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        yield recorded
    finally:
        event.remove(engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("endpoint", ["/api/auth/me", "/api/auth/session"])
def test_session_check_reads_persisted_account_in_one_query(
    client, database, password_hash, statements, endpoint
):
    user_id = add_user(database, password_hash, role=Role.manager)
    client.cookies.set(SESSION_COOKIE, add_session(database, user_id))
    statements.clear()

    response = client.get(endpoint)

    assert response.status_code == 200
    payload = response.json()
    if endpoint == "/api/auth/session":
        assert payload == {
            "me": {
                "id": user_id,
                "name": "Persisted account",
                "email": "saved-account@example.test",
                "phone": None,
                "role": "manager",
                "active": True,
                "receivesAutoAssign": False,
                "onLeave": True,
            }
        }
    else:
        assert payload["id"] == user_id
        assert payload["role"] == "manager"
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")


def test_session_probe_without_cookie_uses_no_database_queries(client, statements):
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json() == {"me": None}
    assert statements == []
    assert client.get("/api/auth/me").status_code == 401
    assert statements == []


@pytest.mark.parametrize("endpoint", ["/api/auth/me", "/api/auth/session"])
@pytest.mark.parametrize(
    "state,reason",
    [
        ("missing", "Session not found"),
        ("expired", "Session expired"),
        ("orphaned", "Account is not active"),
        ("inactive", "Account is not active"),
    ],
)
def test_session_probe_is_anonymous_when_protected_session_check_rejects(
    client, database, password_hash, endpoint, state, reason
):
    if state == "missing":
        token = "not-a-persisted-session"
    else:
        # SQLite permits a deliberately orphaned session so the outer join's
        # missing-account behavior is covered as well as an inactive account.
        user_id = (
            "removed-account"
            if state == "orphaned"
            else add_user(database, password_hash, active=(state != "inactive"))
        )
        token = add_session(database, user_id, expired=(state == "expired"))
    client.cookies.set(SESSION_COOKIE, token)

    response = client.get(endpoint)

    if endpoint == "/api/auth/session":
        assert response.status_code == 200
        assert response.json() == {"me": None}
    else:
        assert response.status_code == 401
        assert response.json() == {"detail": reason}
    if state == "expired":
        with database() as db:
            assert db.get(SessionRow, token) is None


def test_session_probe_observes_revocation_on_the_next_request(
    client, database, password_hash
):
    user_id = add_user(database, password_hash)
    token = add_session(database, user_id)
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/auth/session").json()["me"]["id"] == user_id

    with database() as db:
        db.delete(db.get(SessionRow, token))
        db.commit()

    response = client.get("/api/auth/session")
    assert response.status_code == 200
    assert response.json() == {"me": None}
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("code", [403, 503])
def test_session_probe_preserves_non_authentication_http_errors(
    client, monkeypatch, code
):
    def fail(**kwargs):
        raise HTTPException(code, "Synthetic request failure")

    monkeypatch.setattr(auth, "current_user", fail)
    client.cookies.set(SESSION_COOKIE, "test-session")

    response = client.get("/api/auth/session")

    assert response.status_code == code
    assert response.json() == {"detail": "Synthetic request failure"}


def test_session_probe_does_not_hide_database_failures(client, database):
    client.cookies.set(SESSION_COOKIE, "test-session")
    with patch.object(
        database.class_, "execute", side_effect=RuntimeError("Synthetic database failure")
    ):
        with pytest.raises(RuntimeError, match="Synthetic database failure"):
            client.get("/api/auth/session")


def restart_bootstrap(database, monkeypatch):
    # Simulate a fresh process, including the old seed/reset variables that used to
    # override database values. These must now have no effect on account records.
    monkeypatch.setattr(bootstrap, "_done", False)
    monkeypatch.setattr(bootstrap, "_last_error", None)
    monkeypatch.setenv("SEED_RESET_PASSWORDS", "true")
    for role in ("ADMIN", "MANAGER", "STAFF"):
        monkeypatch.setenv(f"SEED_{role}_PASSWORD", "must-not-overwrite-the-database")
    with database() as db:
        bootstrap.ensure_bootstrap(db)


def test_restart_initializes_pointer_without_creating_accounts(database, monkeypatch):
    restart_bootstrap(database, monkeypatch)
    restart_bootstrap(database, monkeypatch)

    with database() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(AssignmentPointer)) == 1
        assert db.get(AssignmentPointer, 1).last_user_id is None


def test_restart_preserves_accounts_passwords_roles_and_disabled_status(
    database, monkeypatch, password_hash
):
    # Use the formerly seeded addresses, with deliberately different persisted roles
    # and disabled status, so reinstating the old account loop fails this test.
    admin_id = add_user(
        database,
        password_hash,
        email="admin@example.test",
        role=Role.staff,
        active=False,
    )
    add_user(
        database,
        password_hash,
        email="manager@careindiaoverseas.com",
        role=Role.admin,
    )
    with database() as db:
        db.add(AssignmentPointer(id=1, last_user_id=admin_id))
        db.commit()
    before = account_snapshot(database)

    restart_bootstrap(database, monkeypatch)
    restart_bootstrap(database, monkeypatch)

    assert account_snapshot(database) == before
    with database() as db:
        assert db.get(AssignmentPointer, 1).last_user_id == admin_id


def test_bootstrap_failure_rolls_back_and_can_be_retried(database, monkeypatch):
    monkeypatch.setattr(bootstrap, "_done", False)
    monkeypatch.setattr(bootstrap, "_last_error", None)
    with database() as db:
        with (
            patch.object(db, "execute", side_effect=RuntimeError("database unavailable")),
            patch.object(db, "rollback", wraps=db.rollback) as rollback,
        ):
            with pytest.raises(RuntimeError, match="database unavailable"):
                bootstrap.ensure_bootstrap(db)
            rollback.assert_called_once()
        assert bootstrap.bootstrap_state()["seeded"] is False
        bootstrap.ensure_bootstrap(db)
        assert bootstrap.bootstrap_state()["seeded"] is True
        assert bootstrap.bootstrap_state()["seedError"] is None
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_health_recovers_from_failed_startup_without_modifying_accounts(
    database, monkeypatch, password_hash
):
    with patch(
        "app.core.config.get_settings",
        return_value=SimpleNamespace(
            cors_origin_list=["https://testserver"],
            # A path beneath this file cannot be a directory, so importing the
            # app cannot mount real static assets or need filesystem setup.
            static_dir=str(Path(__file__) / "no-static-files"),
            storage_dir=str(Path(__file__) / "no-uploads"),
            veris_ocr_base_url=None,
            veris_ocr_api_key=None,
        ),
    ):
        from app import main

    monkeypatch.setattr(main, "engine", database.kw["bind"])
    monkeypatch.setattr(main, "SessionLocal", database)
    monkeypatch.setattr(
        main, "redis_health",
        lambda: {"enabled": False, "status": "disabled", "error": None},
    )
    monkeypatch.setattr(bootstrap, "_done", False)
    monkeypatch.setattr(bootstrap, "_last_error", None)
    add_user(
        database,
        password_hash,
        email="admin@example.test",
        role=Role.staff,
        active=False,
    )
    before = account_snapshot(database)

    # The startup handler deliberately catches initialization failure, so recovery
    # must happen via readiness rather than requiring an application restart.
    with patch.object(
        database.class_, "execute", side_effect=RuntimeError("temporary startup outage")
    ):
        main.on_startup()
        assert bootstrap.bootstrap_state()["seeded"] is False
        assert bootstrap.bootstrap_state()["seedError"] is not None

        # A working low-level connection alone is insufficient for readiness when
        # the assignment cursor still cannot be initialized.
        failed_probe = main.health()
    assert failed_probe["database"] is True
    assert failed_probe["status"] == "degraded"
    assert failed_probe["seeded"] is False

    recovered = main.health()
    assert recovered["status"] == "ok"
    assert recovered["database"] is True
    assert recovered["seeded"] is True
    assert recovered["seedError"] is None
    assert recovered["accounts"] == 1
    assert account_snapshot(database) == before
    assert main.health()["status"] == "ok"
    with database() as db:
        assert db.scalar(select(func.count()).select_from(AssignmentPointer)) == 1
        assert db.get(AssignmentPointer, 1).last_user_id is None


def test_login_does_not_create_accounts_in_an_empty_database(client, database, monkeypatch):
    monkeypatch.setattr(bootstrap, "_done", False)
    response = client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-wrong-test-password"},
    )

    assert response.status_code == 401
    assert response.json() == {
        "detail": {
            "field": "email",
            "message": "No account found for this email address.",
        }
    }
    assert SESSION_COOKIE not in response.cookies
    with database() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(SessionRow)) == 0
        assert db.scalar(select(func.count()).select_from(AssignmentPointer)) == 0


@pytest.mark.parametrize(
    "email,password,field,message",
    [
        (
            "missing-account@example.test",
            "a-test-only-password",
            "email",
            "No account found for this email address.",
        ),
        ("", "a-test-only-password", "email", "No account found for this email address."),
        ("   ", "a-test-only-password", "email", "No account found for this email address."),
        (
            "saved-account@example.test",
            "a-wrong-test-password",
            "password",
            "Password is incorrect.",
        ),
        ("saved-account@example.test", "", "password", "Password is incorrect."),
    ],
)
def test_failed_login_identifies_field_without_creating_sessions_or_changing_account(
    client, database, password_hash, email, password, field, message
):
    add_user(database, password_hash)
    before = account_snapshot(database)

    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": {"field": field, "message": message}}
    assert SESSION_COOKIE not in response.cookies
    assert account_snapshot(database) == before
    with database() as db:
        assert db.scalar(select(func.count()).select_from(SessionRow)) == 0


def test_login_uses_persisted_password_and_role_without_rewriting_user(
    client, database, monkeypatch, password_hash
):
    user_id = add_user(
        database, password_hash, email="admin@example.test", role=Role.staff
    )
    before = account_snapshot(database)
    monkeypatch.setattr(bootstrap, "_done", False)

    wrong_password = client.post(
        "/api/auth/login",
        json={"email": "admin@example.test", "password": "a-wrong-test-password"},
    )
    assert wrong_password.status_code == 401
    assert wrong_password.json() == {
        "detail": {"field": "password", "message": "Password is incorrect."}
    }
    with database() as db:
        assert db.scalar(select(func.count()).select_from(SessionRow)) == 0
    response = client.post(
        "/api/auth/login",
        json={
            "email": "  ADMIN@EXAMPLE.TEST  ",
            "password": "a-test-only-password",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": user_id,
        "name": "Persisted account",
        "email": "admin@example.test",
        "role": "staff",
        "active": True,
    }
    assert account_snapshot(database) == before
    with database() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        session = db.scalar(select(SessionRow))
        assert session.user_id == user_id
        assert session.id == response.cookies[SESSION_COOKIE]
        assert verify_password("a-test-only-password", db.get(User, user_id).password_hash)
        assert db.scalar(select(func.count()).select_from(AssignmentPointer)) == 0


def test_login_cannot_reactivate_a_disabled_account(
    client, database, monkeypatch, password_hash
):
    add_user(
        database, password_hash, email="admin@example.test", active=False
    )
    before = account_snapshot(database)
    monkeypatch.setattr(bootstrap, "_done", False)

    response = client.post(
        "/api/auth/login",
        json={
            "email": "admin@example.test",
            "password": "a-test-only-password",
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "This account has been deactivated"}
    assert SESSION_COOKIE not in response.cookies
    assert account_snapshot(database) == before
    with database() as db:
        assert db.scalar(select(func.count()).select_from(SessionRow)) == 0


def test_existing_session_observes_database_role_changes_and_deactivation(
    client, database, password_hash
):
    user_id = add_user(database, password_hash, role=Role.admin)
    response = client.post(
        "/api/auth/login",
        json={"email": "saved-account@example.test", "password": "a-test-only-password"},
    )
    assert response.status_code == 200
    assert client.get("/admin-only").status_code == 200
    assert client.get("/api/auth/session").json()["me"]["role"] == "admin"

    with database() as db:
        db.get(User, user_id).role = Role.staff
        db.commit()
    assert client.get("/api/auth/me").json()["role"] == "staff"
    assert client.get("/api/auth/session").json()["me"]["role"] == "staff"
    assert client.get("/admin-only").status_code == 403

    with database() as db:
        db.get(User, user_id).active = False
        db.commit()
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/session").json() == {"me": None}
    assert client.get("/admin-only").status_code == 401


@pytest.mark.parametrize("role", list(Role))
def test_create_user_explicitly_persists_selected_role_and_hashed_password(
    database, monkeypatch, capsys, role
):
    password = "an-explicitly-chosen-test-password"
    answers = iter([password, password])
    monkeypatch.setattr(cli, "SessionLocal", database)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert cli.create_user("  NEW-ACCOUNT@EXAMPLE.TEST  ", "  New account  ", role) == 0

    with database() as db:
        users = list(db.scalars(select(User)))
        assert len(users) == 1
        user = users[0]
        assert user.email == "new-account@example.test"
        assert user.name == "New account"
        assert user.role == role
        assert user.active is True
        assert user.receives_auto_assign == (role is Role.staff)
        assert user.password_hash != password
        assert verify_password(password, user.password_hash)
    output = capsys.readouterr()
    assert password not in output.out + output.err


def test_create_user_refuses_to_overwrite_an_existing_account(
    database, monkeypatch, password_hash
):
    add_user(database, password_hash, role=Role.manager, active=False)
    before = account_snapshot(database)
    monkeypatch.setattr(cli, "SessionLocal", database)

    def unexpected_password_prompt(prompt):
        pytest.fail("Existing accounts must be rejected before requesting a password")

    monkeypatch.setattr(cli.getpass, "getpass", unexpected_password_prompt)

    assert cli.create_user(
        "  SAVED-ACCOUNT@EXAMPLE.TEST ", "Replacement", Role.admin
    ) == 1
    assert account_snapshot(database) == before


@pytest.mark.parametrize("answers", [("", ""), ("first-password", "different-password")])
def test_create_user_requires_a_nonempty_confirmed_password(database, monkeypatch, answers):
    supplied_answers = iter(answers)
    monkeypatch.setattr(cli, "SessionLocal", database)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(supplied_answers))

    assert cli.create_user("new-account@example.test", "New account", Role.admin) == 1
    assert account_snapshot(database) == []


def test_password_reset_preserves_account_settings_and_revokes_only_its_sessions(
    database, monkeypatch, password_hash, capsys
):
    target_id = add_user(
        database,
        password_hash,
        email="admin@example.test",
        role=Role.manager,
        active=False,
    )
    other_id = add_user(database, password_hash)
    with database() as db:
        db.add_all(
            [
                SessionRow(user_id=target_id, expires_at=session_expiry()),
                SessionRow(user_id=target_id, expires_at=session_expiry()),
                SessionRow(user_id=other_id, expires_at=session_expiry()),
            ]
        )
        db.commit()
    before = account_snapshot(database)
    new_password = "a-new-test-only-password"
    answers = iter([new_password, new_password])
    monkeypatch.setattr(cli, "SessionLocal", database)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: next(answers))

    assert cli.set_password("  ADMIN@EXAMPLE.TEST  ") == 0

    after = account_snapshot(database)
    for original, current in zip(before, after):
        if original["id"] == target_id:
            assert current["password_hash"] != original["password_hash"]
            assert verify_password(new_password, current["password_hash"])
            assert not verify_password("a-test-only-password", current["password_hash"])
            for column in User.__table__.columns.keys():
                if column not in ("password_hash", "updated_at"):
                    assert current[column] == original[column]
        else:
            assert current == original
    with database() as db:
        sessions = list(db.scalars(select(SessionRow)))
        assert len(sessions) == 1
        assert sessions[0].user_id == other_id
    output = capsys.readouterr()
    assert new_password not in output.out + output.err
