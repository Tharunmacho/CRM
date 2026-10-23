"""Startup never leaves a developer stuck on a database that only lacks newer migrations."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app import runtime
from app.db import schema_readiness
from app.db.schema_readiness import expected_revisions, is_behind

HEAD = list(expected_revisions())
OLDER = ["0015_attendance_hr"]


@pytest.mark.parametrize("current, behind", [
    ([], False),                     # unversioned: never touched automatically
    (["0007_email_intake"], True),   # known older revision
    (OLDER, True),
    (["future_revision"], False),    # prepared by newer code: never upgraded or downgraded
    (["0007_email_intake", "future_revision"], False),
])
def test_only_a_known_older_database_counts_as_behind(current, behind):
    assert is_behind(current) is behind


class Harness:
    def __init__(self, monkeypatch, *, states, auto):
        self.upgrades, self.states = 0, list(states)
        monkeypatch.setattr(runtime, "get_settings", lambda: SimpleNamespace(crm_auto_migrate=auto))
        monkeypatch.setattr(runtime, "schema_status", lambda _connection: self.states.pop(0))
        monkeypatch.setattr(runtime, "upgrade_to_head", self.upgrade)

        @contextmanager
        def connect():
            yield object()

        monkeypatch.setattr("app.db.session.engine", SimpleNamespace(connect=connect))

    def upgrade(self):
        self.upgrades += 1


def status(current, ok=False):
    return {"status": "ok" if ok else "migration_required", "current": current, "expected": HEAD}


def test_a_matching_database_starts_without_migrating(monkeypatch):
    harness = Harness(monkeypatch, states=[status(HEAD, ok=True)], auto=True)
    runtime.require_current_schema()
    assert harness.upgrades == 0


def test_behind_database_explains_the_exact_command_and_is_left_unchanged(monkeypatch):
    harness = Harness(monkeypatch, states=[status(OLDER)], auto=False)
    with pytest.raises(SystemExit) as stopped:
        runtime.require_current_schema()
    message = str(stopped.value)
    assert "npm run db:migrate" in message and "CRM_AUTO_MIGRATE=true" in message and OLDER[0] in message
    assert harness.upgrades == 0


def test_auto_migrate_applies_pending_migrations_then_starts(monkeypatch, capsys):
    harness = Harness(monkeypatch, states=[status(OLDER), status(HEAD, ok=True)], auto=True)
    runtime.require_current_schema()
    assert harness.upgrades == 1
    assert "Migrations applied" in capsys.readouterr().out


@pytest.mark.parametrize("auto", [False, True])
def test_database_from_newer_code_is_never_migrated_or_downgraded(monkeypatch, auto):
    harness = Harness(monkeypatch, states=[status(["future_revision"])], auto=auto)
    with pytest.raises(SystemExit) as stopped:
        runtime.require_current_schema()
    assert "newer code" in str(stopped.value) and harness.upgrades == 0


def test_unversioned_database_is_never_migrated_automatically(monkeypatch):
    harness = Harness(monkeypatch, states=[status([])], auto=True)
    with pytest.raises(SystemExit):
        runtime.require_current_schema()
    assert harness.upgrades == 0


def test_failed_migration_stops_startup_without_leaking_details(monkeypatch):
    harness = Harness(monkeypatch, states=[status(OLDER)], auto=True)

    def explode():
        raise RuntimeError("postgresql://user:secret-password@host/db")

    monkeypatch.setattr(runtime, "upgrade_to_head", explode)
    with pytest.raises(SystemExit) as stopped:
        runtime.require_current_schema()
    assert "rolled back" in str(stopped.value) and "secret-password" not in str(stopped.value)
    assert harness.upgrades == 0


def test_upgrade_helper_targets_head(monkeypatch):
    calls = []
    monkeypatch.setattr(schema_readiness.command, "upgrade", lambda config, target: calls.append(target))
    schema_readiness.upgrade_to_head()
    assert calls == ["head"]
