"""Schema mismatch is explicit, read-only and recoverable after a coordinated upgrade."""
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core import config
from app.core.security import SESSION_COOKIE
from app.db.schema_readiness import UPDATE_REQUIRED, expected_revisions, schema_status
from test_database_naming import PREVIOUS, _data_snapshot, _migrate, _seed_previous_schema, schema_database


@pytest.mark.parametrize("revision", [None, "0007_email_intake", "future_revision"])
def test_revision_check_does_not_create_or_modify_schema(revision):
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        if revision is not None:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision})
        before = inspect(connection).get_table_names()
        result = schema_status(connection)
        assert result == {"status": "migration_required", "current": [revision] if revision else [], "expected": list(expected_revisions())}
        assert inspect(connection).get_table_names() == before
        if revision:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == revision
    engine.dispose()


def test_matching_revision_is_ready():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
        for revision in expected_revisions():
            connection.execute(text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision})
        assert schema_status(connection)["status"] == "ok"
    engine.dispose()


def test_old_database_health_and_workspace_explain_upgrade_without_writing(schema_database, monkeypatch, tmp_path):
    engine = schema_database
    _migrate(engine, PREVIOUS)
    _seed_previous_schema(engine)
    _migrate(engine, "0007_email_intake")
    settings = config.Settings(_env_file=None, database_url=engine.url.render_as_string(hide_password=False),
                               storage_dir=str(tmp_path), static_dir=str(tmp_path / "no-static"), redis_url=None)
    with patch("app.core.config.get_settings", return_value=settings):
        from app import main
        from app.api.routers import data
        from app.db.session import get_db
        from app.services import bootstrap
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "settings", settings)
    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(main, "find_tesseract", lambda command=None: None)
    monkeypatch.setattr(main, "redis_health", lambda: {"enabled": False, "status": "disabled", "error": None})
    monkeypatch.setattr(data, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "_done", False)
    monkeypatch.setattr(bootstrap, "_last_error", None)

    def database():
        with sessions() as db:
            yield db

    monkeypatch.setitem(main.app.dependency_overrides, get_db, database)
    with engine.connect() as connection:
        before = _data_snapshot(connection, renamed=True)
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.set(SESSION_COOKIE, "schema-existing-session")
        health = client.get("/api/health")
        assert health.status_code == 503
        assert health.json()["database"] is True
        assert health.json()["schema"]["status"] == "migration_required"
        assert health.json()["schema"]["current"] == ["0007_email_intake"]
        workspace = client.get("/api/bootstrap")
        assert workspace.status_code == 503
        assert workspace.json() == {"detail": UPDATE_REQUIRED}
        assert "SELECT" not in workspace.text and "whatsapp" not in workspace.text
        with engine.connect() as connection:
            assert _data_snapshot(connection, renamed=True) == before
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007_email_intake"

        # Explicit upgrade on this guarded disposable DB is the only write here.
        _migrate(engine, "head")
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["schema"]["status"] == "ok"
        workspace = client.get("/api/bootstrap")
        assert workspace.status_code == 200
        assert workspace.json()["candidates"][0]["id"] == "schema-candidate"
        assert workspace.json()["tasks"][0]["id"] == "schema-task"
        assert workspace.json()["me"]["id"] == "schema-user"
