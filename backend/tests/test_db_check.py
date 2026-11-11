"""The diagnostic must report failures safely and never start workers or migrate."""
from contextlib import contextmanager
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy.exc import OperationalError

from app import db_check


@pytest.fixture
def diagnostic(monkeypatch):
    monkeypatch.setattr(db_check, "get_settings", lambda: SimpleNamespace(
        database_url="postgresql+psycopg://private-user:private-password@db.example:5433/crm",
        crm_auto_migrate=True,
    ))
    calls = {}

    @contextmanager
    def connect():
        if calls.get("error"):
            raise calls["error"]
        yield SimpleNamespace(execute=lambda statement: calls.setdefault("statement", str(statement)))

    def create_engine(url, **kwargs):
        calls["connect_args"] = kwargs["connect_args"]
        return SimpleNamespace(connect=connect, dispose=lambda: calls.update(disposed=True))

    monkeypatch.setattr(db_check, "create_engine", create_engine)
    monkeypatch.setattr(db_check, "schema_status", lambda _: calls["schema"])
    return calls


def test_current_schema_is_read_only_even_with_auto_migrate(diagnostic, capsys):
    diagnostic["schema"] = {"status": "ok", "current": ["v1"], "expected": ["v1"]}
    assert db_check.main() == 0
    output = capsys.readouterr().out
    assert '"host": "db.example", "port": 5433' in output
    assert "PostgreSQL connection: OK" in output
    assert "private-user" not in output and "private-password" not in output
    assert "default_transaction_read_only=on" in diagnostic["connect_args"]["options"]
    assert "statement_timeout=5000" in diagnostic["connect_args"]["options"]
    assert diagnostic["connect_args"]["connect_timeout"] == 5
    assert diagnostic["disposed"]


def test_schema_mismatch_returns_failure(diagnostic, capsys):
    diagnostic["schema"] = {"status": "migration_required", "current": ["v0"], "expected": ["v1"]}
    assert db_check.main() == 1
    assert "Deploy matching code and migrations" in capsys.readouterr().out
    assert diagnostic["disposed"]


def test_unreachable_database_does_not_leak_driver_error(diagnostic, capsys):
    diagnostic["error"] = OperationalError(
        None, None, psycopg.errors.ConnectionTimeout("private-user private-password timeout expired")
    )
    assert db_check.main() == 1
    output = capsys.readouterr().out
    assert "ConnectionTimeout" in output and "hosting migration" in output
    assert "private-user" not in output and "private-password" not in output
    assert diagnostic["disposed"]


def test_invalid_configuration_does_not_leak_validation_values(monkeypatch, capsys):
    def invalid():
        raise ValueError("private-password invalid settings")
    monkeypatch.setattr(db_check, "get_settings", invalid)
    assert db_check.main() == 1
    assert "private-password" not in capsys.readouterr().out
