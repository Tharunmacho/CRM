"""API timing without logging query parameters, credentials or record contents."""

import logging
from contextvars import ContextVar
from time import perf_counter

from sqlalchemy import event
from sqlalchemy.engine import Engine
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("uvicorn.error")

# Requests slower than this are written to the log with their query count, so a slow screen shows at a
# glance whether it waited on many sequential statements (each a network round trip to a hosted database)
# or on one slow statement. The statements themselves are never logged.
SLOW_REQUEST_MS = 800

# Durations (seconds) of the statements run for the request in flight. A list, so threads that inherit the
# context (the sync endpoint's worker thread, parallel_reads) all append to the same one.
_statements: ContextVar[list[float] | None] = ContextVar("request_statements", default=None)


@event.listens_for(Engine, "before_cursor_execute")
def _statement_started(conn, cursor, statement, parameters, context, executemany):
    if _statements.get() is not None:
        conn.info.setdefault("crm_started", []).append(perf_counter())


@event.listens_for(Engine, "after_cursor_execute")
def _statement_finished(conn, cursor, statement, parameters, context, executemany):
    recorded = _statements.get()
    started = conn.info.get("crm_started")
    if recorded is not None and started:
        recorded.append(perf_counter() - started.pop())


class ApiTimingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)

        started = perf_counter()
        statements: list[float] = []
        token = _statements.set(statements)

        async def timed_send(message: Message):
            if message["type"] == "http.response.start":
                elapsed = (perf_counter() - started) * 1000
                headers = MutableHeaders(scope=message)
                database = sum(statements) * 1000
                timing = f"app;dur={elapsed:.1f}"
                if statements:
                    timing += f', db;dur={database:.1f};desc="{len(statements)} queries"'
                headers.append("Server-Timing", timing)
                if elapsed >= SLOW_REQUEST_MS:
                    route = scope.get("route")
                    # Use the route template, never an ID or user-supplied URL.
                    path = getattr(route, "path", "/api/<unmatched>")
                    detail = f" queries={len(statements)} db={database:.0f}ms" if statements else ""
                    log.info("Slow API: %s %s status=%s duration=%.0fms%s",
                             scope["method"], path, message["status"], elapsed, detail)
            await send(message)

        try:
            await self.app(scope, receive, timed_send)
        finally:
            _statements.reset(token)
