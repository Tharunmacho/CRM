"""The pool keeper proves idle connections alive and tops the pool up, off the request path."""
import time

from sqlalchemy import text

from app.db.session import keep_pool_round
from test_database_naming import schema_database  # noqa: F401


def test_round_pings_idle_connections_and_tops_the_pool_up(schema_database):
    engine = schema_database
    held = [engine.raw_connection() for _ in range(3)]
    for connection in held:
        connection.close()  # three idle pooled connections
    assert engine.pool.checkedin() == 3

    result = keep_pool_round(engine, resident=5, ping_after=0)
    assert result["pinged"] == 3 and result["dropped"] == 0
    assert result["opened"] == 2
    assert engine.pool.checkedin() == 5 and engine.pool.checkedout() == 0


def test_round_discards_a_connection_the_server_closed(schema_database):
    engine = schema_database
    victim, other = engine.raw_connection(), engine.raw_connection()
    with engine.connect() as admin:
        pid = victim.driver_connection.info.backend_pid
        admin.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
    victim.close()
    other.close()

    result = keep_pool_round(engine, resident=1, ping_after=0)
    assert result["dropped"] == 1  # the dead one was found by the keeper, not by a user's request
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1  # and requests still get a working connection


def test_recently_used_connections_are_left_alone(schema_database):
    engine = schema_database
    connection = engine.raw_connection()
    connection.info["last_used"] = time.monotonic()  # what the pool's check-in hook stamps
    connection.close()
    # Not idle long enough to need proving, so a busy desk's hot connection is never taken out of service.
    assert keep_pool_round(engine, resident=1, ping_after=3600)["pinged"] == 0
