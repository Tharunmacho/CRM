"""API timing without logging query parameters, credentials or record contents."""

import logging
from time import perf_counter

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

log = logging.getLogger("uvicorn.error")


class ApiTimingMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)

        started = perf_counter()

        async def timed_send(message: Message):
            if message["type"] == "http.response.start":
                elapsed = (perf_counter() - started) * 1000
                headers = MutableHeaders(scope=message)
                headers.append("Server-Timing", f"app;dur={elapsed:.1f}")
                if elapsed >= 1000:
                    route = scope.get("route")
                    # Use the route template, never an ID or user-supplied URL.
                    path = getattr(route, "path", "/api/<unmatched>")
                    log.info("Slow API: %s %s status=%s duration=%.0fms",
                             scope["method"], path, message["status"], elapsed)
            await send(message)

        await self.app(scope, receive, timed_send)
