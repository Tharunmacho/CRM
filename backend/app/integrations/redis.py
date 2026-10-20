"""Optional Redis connectivity, shared by readiness checks and future consumers."""

from threading import Lock
from typing import Literal, TypedDict

from redis import ConnectionPool, Redis
from redis.backoff import NoBackoff
from redis.connection import parse_url
from redis.retry import Retry

from app.core.config import get_settings


class RedisHealth(TypedDict):
    enabled: bool
    status: Literal["disabled", "ok", "unavailable"]
    error: str | None


_client: Redis | None = None
_client_lock = Lock()


def get_redis_client() -> Redis | None:
    """Return the shared lazy client, or None when REDIS_URL is blank."""
    url = (get_settings().redis_url or "").strip()
    if not url:
        return None

    global _client
    with _client_lock:
        if _client is None:
            # Parse first so URL query options cannot override our bounds.
            options = parse_url(url)
            options.update(
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry=Retry(NoBackoff(), 0),
                retry_on_timeout=False,
                retry_on_error=[],
                decode_responses=True,
                max_connections=10,
                protocol=2,
                health_check_interval=0,
            )
            pool = ConnectionPool(**options)
            # from_pool transfers ownership: close() also disconnects the pool.
            _client = Redis.from_pool(pool)
        return _client


def redis_health() -> RedisHealth:
    """PING configured Redis without exposing credentials or driver errors."""
    try:
        client = get_redis_client()
        if client is None:
            return {"enabled": False, "status": "disabled", "error": None}
        if client.ping():
            return {"enabled": True, "status": "ok", "error": None}
    except Exception:
        # Invalid URLs, authentication failures and outages have the same safe
        # public response. Driver exception strings can contain credentials.
        pass
    return {
        "enabled": True,
        "status": "unavailable",
        "error": "Redis is unavailable.",
    }


def close_redis() -> None:
    """Release all pooled connections and allow a later client to be rebuilt."""
    global _client
    with _client_lock:
        client = _client
        _client = None
    if client is not None:
        client.close()
