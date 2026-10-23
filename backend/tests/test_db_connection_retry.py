"""A remote database can drop one brand-new connection; the app must retry, not fail the request."""
import psycopg
import pytest
from sqlalchemy.exc import OperationalError

from app.db import session as subject


def test_transient_drop_is_retried_then_succeeds(monkeypatch):
    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise psycopg.OperationalError("consuming input failed: server closed the connection unexpectedly")
        return "connection"

    monkeypatch.setattr(psycopg, "connect", flaky)
    monkeypatch.setattr(subject.time, "sleep", lambda _: None)
    assert subject._connect_with_retry(subject.engine.dialect, None, (), {}) == "connection"
    assert len(attempts) == 3


def test_permanent_failures_are_not_retried(monkeypatch):
    attempts = []

    def refused(*args, **kwargs):
        attempts.append(1)
        raise psycopg.OperationalError('connection failed: FATAL: password authentication failed for user "x"')

    monkeypatch.setattr(psycopg, "connect", refused)
    monkeypatch.setattr(subject.time, "sleep", lambda _: None)
    with pytest.raises(psycopg.OperationalError):
        subject._connect_with_retry(subject.engine.dialect, None, (), {})
    assert len(attempts) == 1


def test_gives_up_after_the_attempt_limit(monkeypatch):
    attempts = []

    def down(*args, **kwargs):
        attempts.append(1)
        raise psycopg.OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(psycopg, "connect", down)
    monkeypatch.setattr(subject.time, "sleep", lambda _: None)
    with pytest.raises(psycopg.OperationalError):
        subject._connect_with_retry(subject.engine.dialect, None, (), {})
    assert len(attempts) == subject.CONNECT_ATTEMPTS


def test_first_connection_helper_retries_a_dropped_handshake(monkeypatch):
    calls = []

    class Engine:
        def connect(self):
            calls.append(1)
            if len(calls) == 1:
                raise OperationalError("show standard_conforming_strings", {}, Exception("server closed the connection unexpectedly"))
            return "ok"

    monkeypatch.setattr(subject, "engine", Engine())
    monkeypatch.setattr(subject.time, "sleep", lambda _: None)
    assert subject.connect_with_retry() == "ok" and len(calls) == 2
