"""Redis readiness and pool lifecycle without external sockets or database access."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from redis.exceptions import AuthenticationError, ConnectionError, TimeoutError

from app.integrations import redis as redis_connection


TEST_URL = "redis://test-user:synthetic-redis-password@cache.example.test:6379/4"
DISABLED = {"enabled": False, "status": "disabled", "error": None}
AVAILABLE = {"enabled": True, "status": "ok", "error": None}
UNAVAILABLE = {
    "enabled": True,
    "status": "unavailable",
    "error": "Redis is unavailable.",
}


@pytest.fixture(autouse=True)
def isolated_redis(monkeypatch):
    settings = SimpleNamespace(redis_url=None)
    monkeypatch.setattr(redis_connection, "get_settings", lambda: settings)
    monkeypatch.setattr(redis_connection, "_client", None)
    pool_type = redis_connection.ConnectionPool
    pools = []

    def refuse_network(*args, **kwargs):
        raise AssertionError("These tests must not obtain a real Redis connection")

    # Pool construction and URL parsing are exercised normally. Only connection
    # checkout is blocked, before the driver can open any network socket.
    monkeypatch.setattr(pool_type, "get_connection", refuse_network)

    def create_pool(**options):
        pool = pool_type(**options)
        pools.append(pool)
        return pool

    factory = Mock(side_effect=create_pool)
    monkeypatch.setattr(redis_connection, "ConnectionPool", factory)
    try:
        yield SimpleNamespace(settings=settings, pools=pools, factory=factory)
    finally:
        redis_connection.close_redis()


@pytest.mark.parametrize("value", [None, "", " \n "])
def test_optional_unset_redis_creates_no_pool_or_client(isolated_redis, value):
    isolated_redis.settings.redis_url = value
    assert redis_connection.get_redis_client() is None
    assert redis_connection.redis_health() == DISABLED
    isolated_redis.factory.assert_not_called()


def test_repeated_health_checks_share_one_lazy_client_and_pool(isolated_redis, monkeypatch):
    isolated_redis.settings.redis_url = TEST_URL
    client = redis_connection.get_redis_client()
    ping = Mock(return_value=True)
    monkeypatch.setattr(client, "ping", ping)

    assert redis_connection.get_redis_client() is client
    assert redis_connection.redis_health() == AVAILABLE
    assert redis_connection.redis_health() == AVAILABLE
    assert redis_connection.get_redis_client() is client
    assert len(isolated_redis.pools) == 1
    isolated_redis.factory.assert_called_once()
    assert ping.call_count == 2


@pytest.mark.parametrize("error_type", [AuthenticationError, ConnectionError, TimeoutError])
def test_connection_errors_are_sanitized(
    isolated_redis, monkeypatch, caplog, error_type
):
    isolated_redis.settings.redis_url = TEST_URL
    client = redis_connection.get_redis_client()
    # Driver errors may embed credentials and the complete connection URL.
    monkeypatch.setattr(
        client, "ping", Mock(side_effect=error_type(f"Cannot use {TEST_URL}"))
    )

    response = redis_connection.redis_health()

    assert response == UNAVAILABLE
    assert "synthetic-redis-password" not in repr(response) + caplog.text
    assert "cache.example.test" not in repr(response) + caplog.text
    assert TEST_URL not in repr(response) + caplog.text


def test_false_ping_response_reports_unavailable(isolated_redis, monkeypatch):
    isolated_redis.settings.redis_url = TEST_URL
    monkeypatch.setattr(redis_connection.get_redis_client(), "ping", lambda: False)
    assert redis_connection.redis_health() == UNAVAILABLE


@pytest.mark.parametrize(
    "invalid",
    [
        "https://test-user:synthetic-redis-password@cache.example.test/0",
        "redis://test-user:synthetic-redis-password@cache.example.test:not-a-port/0",
        TEST_URL + "?socket_timeout=not-a-number",
    ],
)
def test_invalid_urls_are_sanitized_without_creating_a_pool(
    isolated_redis, caplog, invalid
):
    isolated_redis.settings.redis_url = invalid

    response = redis_connection.redis_health()

    assert response == UNAVAILABLE
    isolated_redis.factory.assert_not_called()
    assert "synthetic-redis-password" not in repr(response) + caplog.text
    assert "cache.example.test" not in repr(response) + caplog.text


def test_url_query_cannot_override_bounded_timeouts_or_enable_retries(isolated_redis):
    isolated_redis.settings.redis_url = TEST_URL + (
        "?socket_timeout=999&socket_connect_timeout=999&retry_on_timeout=True"
        "&retry_on_error=all&max_connections=999&protocol=3"
        "&health_check_interval=999&decode_responses=False"
    )
    client = redis_connection.get_redis_client()
    pool = client.connection_pool
    options = pool.connection_kwargs
    assert options["socket_timeout"] == 2.0
    assert options["socket_connect_timeout"] == 2.0
    assert options["retry_on_timeout"] is False
    assert options["retry_on_error"] == []
    assert options["decode_responses"] is True
    assert options["protocol"] == 2
    assert options["health_check_interval"] == 0
    assert pool.max_connections == 10

    attempts = []

    def failed_operation():
        attempts.append("attempt")
        raise ConnectionError("Synthetic failure; no network operation")

    with pytest.raises(ConnectionError):
        options["retry"].call_with_retry(failed_operation, lambda error: None)
    assert attempts == ["attempt"], "The health client must not retry a failed command"


def test_close_disconnects_owned_pool_and_allows_a_new_client(isolated_redis, monkeypatch):
    isolated_redis.settings.redis_url = TEST_URL
    client = redis_connection.get_redis_client()
    pool = client.connection_pool
    disconnect = Mock(wraps=pool.disconnect)
    monkeypatch.setattr(pool, "disconnect", disconnect)

    redis_connection.close_redis()
    disconnect.assert_called_once()
    redis_connection.close_redis()
    disconnect.assert_called_once()

    replacement = redis_connection.get_redis_client()
    assert replacement is not client
    assert replacement.connection_pool is not pool
    assert len(isolated_redis.pools) == 2


def _health_application(monkeypatch, *, database_ready=True, bootstrap_ready=True):
    # Importing main must not read backend/.env even when this file is tested alone.
    settings = SimpleNamespace(
        database_url="postgresql+psycopg://unused@127.0.0.1:1/unused",
        cors_origin_list=["https://testserver"],
        static_dir=str(Path(__file__) / "no-static-files"),
        storage_dir=str(Path(__file__) / "no-upload-files"),
        veris_ocr_base_url=None,
        veris_ocr_api_key=None,
        redis_url=None,
    )
    with patch("app.core.config.get_settings", return_value=settings):
        from app import main

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            # Both select 1 and the user count are local stubs.
            return SimpleNamespace(scalar=lambda: 2)

    connect = (
        Connection if database_ready
        else Mock(side_effect=RuntimeError("Synthetic database outage"))
    )
    monkeypatch.setattr(main, "engine", SimpleNamespace(connect=connect))
    monkeypatch.setattr(main, "schema_status", lambda connection: {"status": "ok", "current": ["test"], "expected": ["test"]})
    monkeypatch.setattr(
        main, "bootstrap_state",
        lambda: {
            "seeded": bootstrap_ready,
            "seedError": None if bootstrap_ready else "Synthetic bootstrap failure",
        },
    )
    monkeypatch.setattr(main, "SessionLocal", Connection)
    monkeypatch.setattr(
        main, "ensure_bootstrap",
        Mock(side_effect=RuntimeError("Synthetic bootstrap failure")),
    )
    monkeypatch.setattr(main, "redis_health", redis_connection.redis_health)
    monkeypatch.setattr(main, "close_redis", redis_connection.close_redis)
    application = FastAPI()
    application.add_api_route("/api/health", main.health)
    application.add_event_handler("shutdown", main.on_shutdown)
    return application


@pytest.mark.parametrize(
    ("redis_status", "expected", "overall", "http_status"),
    [
        ("disabled", DISABLED, "ok", 200),
        ("ok", AVAILABLE, "ok", 200),
        ("unavailable", UNAVAILABLE, "degraded", 503),
    ],
)
def test_health_endpoint_reports_redis_readiness_and_shutdown_closes_pool(
    isolated_redis, monkeypatch, redis_status, expected, overall, http_status
):
    disconnect = None
    if redis_status != "disabled":
        isolated_redis.settings.redis_url = TEST_URL
        client = redis_connection.get_redis_client()
        ping = (
            Mock(return_value=True) if redis_status == "ok"
            else Mock(side_effect=ConnectionError(f"Unreachable {TEST_URL}"))
        )
        monkeypatch.setattr(client, "ping", ping)
        disconnect = Mock(wraps=client.connection_pool.disconnect)
        monkeypatch.setattr(client.connection_pool, "disconnect", disconnect)

    with TestClient(_health_application(monkeypatch)) as client:
        response = client.get("/api/health")
        assert response.status_code == http_status
        payload = response.json()
        assert payload["status"] == overall
        assert payload["redis"] == expected
        assert payload["database"] is True
        assert payload["accounts"] == 2
        assert "synthetic-redis-password" not in response.text
        if disconnect is not None:
            disconnect.assert_not_called()
    if disconnect is not None:
        disconnect.assert_called_once()
    else:
        isolated_redis.factory.assert_not_called()


@pytest.mark.parametrize(
    ("database_ready", "bootstrap_ready"), [(False, True), (True, False)]
)
def test_healthy_redis_does_not_mask_unavailable_application_dependencies(
    isolated_redis, monkeypatch, database_ready, bootstrap_ready
):
    isolated_redis.settings.redis_url = TEST_URL
    monkeypatch.setattr(redis_connection.get_redis_client(), "ping", lambda: True)
    application = _health_application(
        monkeypatch, database_ready=database_ready, bootstrap_ready=bootstrap_ready
    )

    with TestClient(application) as client:
        response = client.get("/api/health")

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "degraded"
    assert payload["redis"] == AVAILABLE
    assert payload["database"] is database_ready
    assert payload["seeded"] is bootstrap_ready
