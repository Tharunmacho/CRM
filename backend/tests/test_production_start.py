"""Exercise the production launcher without reading .env or contacting a DB.

Only a temporary SQLite alembic_version table is used. Subprocesses and API exec
are intercepted, and the database module is replaced before running the launcher.
"""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from sqlalchemy import create_engine, event, inspect, text

from app.core import config


BACKEND = Path(__file__).resolve().parents[1]
LAUNCHER_PATH = BACKEND.parent / "scripts" / "start-production.py"


class ApiStarted(Exception):
    """Raised instead of replacing the test runner with Uvicorn."""


@pytest.fixture
def launch(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("crm_startup_under_test", LAUNCHER_PATH)
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)

    workspace = tmp_path / "workspace"
    static_dir = workspace / "static"
    static_dir.mkdir(parents=True)
    (static_dir / "index.html").write_text("<html>Test frontend</html>", encoding="utf-8")
    monkeypatch.setattr(launcher, "ROOT", workspace)
    # Keep the real migration scripts available for resolving the required revision;
    # only their metadata is read. No Alembic env.py or migration is executed.
    monkeypatch.setattr(launcher, "BACKEND", BACKEND)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(sys, "path", list(sys.path))
    for key in ("DATABASE_URL", "PORT", "STATIC_DIR", "STORAGE_DIR", "RUN_MIGRATIONS_ON_START", "EMAIL_INGESTION_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://unused@127.0.0.1:1/unused")
    # Use the real boolean parser on each call, without the application cache or
    # backend/.env. The engine below is independently replaced with local SQLite.
    monkeypatch.setattr(config, "get_settings", lambda: config.Settings(_env_file=None))

    heads = set(ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_heads())
    assert len(heads) == 1, "The launcher fixtures expect one current migration head"
    head = next(iter(heads))
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'schema.sqlite'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version VALUES (:head)"), {"head": head})

    fake_database = ModuleType("app.db.session")
    fake_database.engine = engine
    monkeypatch.setitem(sys.modules, "app.db.session", fake_database)
    calls = []
    observed = []

    def before_execute(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and "alembic_version.version_num" in statement:
            observed.append("read-revision")

    event.listen(engine, "before_cursor_execute", before_execute)
    real_dispose = engine.dispose

    def dispose():
        observed.append("dispose")
        real_dispose()

    monkeypatch.setattr(engine, "dispose", dispose)

    def unexpected_subprocess(*args, **kwargs):
        pytest.fail("Startup must not run migrations unless explicitly enabled")

    def api_exec(executable, arguments):
        observed.append("api")
        calls.append({
            "executable": executable,
            "arguments": arguments,
            "cwd": Path.cwd(),
            "path": list(sys.path),
            "static": os.environ["STATIC_DIR"],
            "storage": os.environ["STORAGE_DIR"],
        })
        raise ApiStarted()

    monkeypatch.setattr(launcher.subprocess, "run", unexpected_subprocess)
    monkeypatch.setattr(launcher.os, "execv", api_exec)

    def revision(value):
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            if value is not None:
                connection.execute(
                    text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": value}
                )

    def schema_state():
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            rows = (
                list(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
                if "alembic_version" in tables else []
            )
            return tables, rows

    try:
        yield SimpleNamespace(
            main=launcher.main,
            module=launcher,
            workspace=workspace,
            engine=engine,
            head=head,
            revision=revision,
            schema_state=schema_state,
            calls=calls,
            observed=observed,
        )
    finally:
        event.remove(engine, "before_cursor_execute", before_execute)
        real_dispose()


@pytest.mark.parametrize("custom", [False, True])
def test_matching_schema_execs_api_with_correct_paths_and_port(launch, monkeypatch, custom):
    if custom:
        static_dir = launch.workspace / "custom-frontend"
        static_dir.mkdir()
        (static_dir / "index.html").write_text("Custom frontend", encoding="utf-8")
        storage_dir = launch.workspace / "mounted-data" / "documents"
        monkeypatch.setenv("STATIC_DIR", str(static_dir))
        monkeypatch.setenv("STORAGE_DIR", str(storage_dir))
        monkeypatch.setenv("PORT", "9017")
    else:
        static_dir = launch.workspace / "static"
        storage_dir = launch.workspace / "data" / "uploads"

    before = launch.schema_state()
    with pytest.raises(ApiStarted):
        launch.main()

    assert len(launch.calls) == 1
    call = launch.calls[0]
    assert call["executable"] == sys.executable
    assert call["arguments"] == [sys.executable, "-m", "app.runtime"]
    assert call["cwd"] == BACKEND
    # Alembic can prepend its own configured script paths while resolving heads.
    # The launcher's backend import directory must remain available afterward.
    assert str(BACKEND) in call["path"]
    assert Path(call["static"]) == static_dir
    assert Path(call["storage"]) == storage_dir
    assert storage_dir.is_dir()
    assert launch.schema_state() == before
    assert launch.observed.index("read-revision") < launch.observed.index("dispose")
    assert launch.observed.index("dispose") < launch.observed.index("api")


@pytest.mark.parametrize("value", ["true", "1", "on", "y", "t", "YES"])
def test_enabled_email_uses_supervisor_after_schema_validation(launch, monkeypatch, value):
    monkeypatch.setenv("EMAIL_INGESTION_ENABLED", value)
    with pytest.raises(ApiStarted):
        launch.main()
    assert launch.calls[0]["arguments"] == [sys.executable, "-m", "app.runtime"]
    assert launch.observed.index("read-revision") < launch.observed.index("api")


@pytest.mark.parametrize("value", ["false", "0", "off", "n", "f", "NO"])
def test_disabled_email_keeps_task_and_document_supervisor(launch, monkeypatch, value):
    monkeypatch.setenv("EMAIL_INGESTION_ENABLED", value)

    with pytest.raises(ApiStarted):
        launch.main()

    assert launch.calls[0]["arguments"] == [sys.executable, "-m", "app.runtime"]


def test_launcher_uses_parsed_settings_when_flag_is_not_in_process_environment(launch, monkeypatch):
    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=True))

    with pytest.raises(ApiStarted):
        launch.main()

    assert launch.calls[0]["arguments"] == [sys.executable, "-m", "app.runtime"]


@pytest.mark.parametrize("state", ["old", "empty-version", "unversioned"])
def test_schema_mismatch_fails_without_running_migrations_or_api(launch, state):
    if state == "old":
        launch.revision("0005_candidate_ocr_data")
    elif state == "empty-version":
        launch.revision(None)
    else:
        with launch.engine.begin() as connection:
            connection.execute(text("DROP TABLE alembic_version"))
    before = launch.schema_state()

    with pytest.raises(SystemExit, match="Database migration required") as failure:
        launch.main()

    assert "No schema changes were made" in str(failure.value)
    assert launch.calls == []
    assert launch.schema_state() == before
    assert "dispose" in launch.observed


def test_explicit_migration_runs_before_schema_check_and_api(launch, monkeypatch):
    launch.revision("0005_candidate_ocr_data")
    monkeypatch.setenv("RUN_MIGRATIONS_ON_START", "1")
    migrations = []

    def migrate(command, *, check):
        launch.observed.append("migrate")
        migrations.append((command, check, Path.cwd()))
        launch.revision(launch.head)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(launch.module.subprocess, "run", migrate)
    with pytest.raises(ApiStarted):
        launch.main()

    assert migrations == [(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        True,
        BACKEND,
    )]
    assert launch.observed.index("migrate") < launch.observed.index("read-revision")
    assert launch.observed.index("read-revision") < launch.observed.index("api")
    assert launch.schema_state() == (["alembic_version"], [launch.head])


def test_failed_migration_aborts_before_schema_check_or_api(launch, monkeypatch):
    launch.revision("0005_candidate_ocr_data")
    monkeypatch.setenv("RUN_MIGRATIONS_ON_START", "1")
    before = launch.schema_state()

    def failed_migration(command, *, check):
        assert check is True
        launch.observed.append("migrate-failed")
        raise subprocess.CalledProcessError(7, command)

    monkeypatch.setattr(launch.module.subprocess, "run", failed_migration)
    with pytest.raises(subprocess.CalledProcessError) as failure:
        launch.main()

    assert failure.value.returncode == 7
    assert launch.observed == ["migrate-failed"]
    assert launch.calls == []
    assert launch.schema_state() == before


def test_successful_migration_command_must_still_produce_the_current_schema(launch, monkeypatch):
    launch.revision("0005_candidate_ocr_data")
    monkeypatch.setenv("RUN_MIGRATIONS_ON_START", "1")
    monkeypatch.setattr(
        launch.module.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )

    with pytest.raises(SystemExit, match="Database migration required"):
        launch.main()
    assert launch.calls == []


def test_missing_database_url_fails_before_any_database_or_process_action(launch, monkeypatch):
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(SystemExit, match="Set DATABASE_URL"):
        launch.main()
    assert launch.observed == []
    assert launch.calls == []


def test_missing_frontend_fails_before_any_database_or_process_action(launch):
    (launch.workspace / "static" / "index.html").unlink()
    with pytest.raises(SystemExit, match="Built frontend missing"):
        launch.main()
    assert launch.observed == []
    assert launch.calls == []
    assert not (launch.workspace / "data").exists()


@pytest.mark.parametrize("port", ["", "0", "-1", "65536", "8000.5", "not-a-port", " 8000"])
def test_invalid_port_fails_before_any_database_or_process_action(launch, monkeypatch, port):
    monkeypatch.setenv("PORT", port)
    with pytest.raises(SystemExit, match="PORT must be an integer"):
        launch.main()
    assert launch.observed == []
    assert launch.calls == []


@pytest.mark.parametrize("value", ["yes", "true", "", "2"])
def test_migration_setting_requires_an_explicit_supported_value(launch, monkeypatch, value):
    monkeypatch.setenv("RUN_MIGRATIONS_ON_START", value)
    with pytest.raises(SystemExit, match="RUN_MIGRATIONS_ON_START must be 0 or 1"):
        launch.main()
    assert launch.observed == []
    assert launch.calls == []
