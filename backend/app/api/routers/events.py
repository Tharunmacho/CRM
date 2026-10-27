"""GET /api/events: a Server-Sent Events stream that tells the signed-in desk what changed, the moment it commits.

The event names the browser sees:
  hello   once, on connect: {"serverTime", "heartbeatMs"}.
  change  {"topics": [...], "ids"?: {...}, "at": <epoch ms>, "resync"?: true}. Topic names only (see change_feed.py);
          a browser refetches the data itself through the ordinary, permission-checked API.
  ping    every HEARTBEAT_SECONDS, so a proxy that closes idle connections and a browser that lost the network
          both notice. The sign-in is re-checked here too.
  bye     the account was signed out or deactivated; the stream then ends.

The stream never holds a database connection: it checks the session on a short-lived one, then only waits.
"""

from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Cookie, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from starlette.requests import Request

from app.core.security import SESSION_COOKIE
from app.db.session import SessionLocal
from app.services import change_feed
from app.services.live_hub import CLOSE, RECHECK, Subscriber, hub

router = APIRouter(prefix="/api", tags=["events"])

HEARTBEAT_SECONDS = 20
COALESCE_SECONDS = 0.25  # events closer together than this reach the browser as one


def _identify(session_id: str | None) -> tuple[str, str] | None:
    """(user id, role) for a live session, using the same rules as every other endpoint; None if not signed in."""
    if not session_id:
        return None
    from app.core.deps import current_user

    try:
        with SessionLocal() as db:
            user = current_user(db=db, crm_session=session_id)
            return user.id, getattr(user.role, "value", str(user.role))
    except HTTPException:
        return None


def _still_same_person(identity: tuple[str, str] | None, subscriber: Subscriber) -> bool:
    """False when the session ended or the account now has another role, so old rights are never kept open."""
    return identity is not None and identity[1] == subscriber.role


def _frame(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _stream(subscriber: Subscriber, session_id: str, request: Request):
    queue = subscriber.queue
    subscriber.started = True
    try:
        yield f"retry: 3000\n\n{_frame('hello', {'serverTime': int(time.time() * 1000), 'heartbeatMs': HEARTBEAT_SECONDS * 1000})}"
        last_sent = 0.0
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                if not _still_same_person(await run_in_threadpool(_identify, session_id), subscriber):
                    yield _frame("bye", {"reason": "signed-out"})
                    return
                yield _frame("ping", {"at": int(time.time() * 1000)})
                continue

            if item is CLOSE:
                return
            recheck = item is RECHECK
            pending: dict | None = None if recheck else item

            if not recheck:
                # Coalesce a burst (a bulk import commits many rows): wait out the window, then merge what queued.
                wait = last_sent + COALESCE_SECONDS - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)
                while not queue.empty():
                    more = queue.get_nowait()
                    if more is CLOSE:
                        return
                    if more is RECHECK:
                        recheck = True
                    else:
                        pending = more if pending is None else change_feed.merge_events(pending, more)

            if recheck and not _still_same_person(await run_in_threadpool(_identify, session_id), subscriber):
                yield _frame("bye", {"reason": "signed-out"})
                return
            if subscriber.behind:  # the queue overflowed: one resync replaces whatever was dropped
                subscriber.behind = False
                pending = {"topics": [change_feed.RESYNC_TOPIC], "resync": True, "at": int(time.time() * 1000)}
            if pending:
                last_sent = time.monotonic()
                yield _frame("change", pending)
    finally:
        hub.unsubscribe(subscriber)


@router.get("/events")
async def events(request: Request, crm_session: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
    identity = await run_in_threadpool(_identify, crm_session)
    if identity is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    if not hub.listening:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Live updates are not available right now.", headers={"Retry-After": "30"})
    subscriber = hub.subscribe(*identity)
    if subscriber is None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many live connections for this account.", headers={"Retry-After": "60"})
    return StreamingResponse(
        _stream(subscriber, crm_session, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",  # no-transform keeps proxies from compressing/buffering
            "X-Accel-Buffering": "no",  # nginx / Traefik: send each event as it is written
            "Connection": "keep-alive",
        },
    )
