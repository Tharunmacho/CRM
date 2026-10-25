"""Per-process fan-out of database change notifications to open Server-Sent-Event streams.

One daemon thread holds a dedicated psycopg connection (autocommit) that has run `LISTEN crm_changes`. Each
notification it receives is parsed and handed to the event loop with `call_soon_threadsafe`; request threads
never wait on it and it never takes a connection from the SQLAlchemy pool. If the connection drops it is
reopened with exponential backoff and every subscriber is told to resynchronise, because notifications sent
while it was away are gone.

Several server processes each run their own hub; PostgreSQL delivers every NOTIFY to all of them, so each
fans out to the browsers connected to it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field

from app.services import change_feed

log = logging.getLogger("uvicorn.error")

MAX_STREAMS_PER_USER = 8
QUEUE_LIMIT = 64
PING_SECONDS = 30  # the listener checks its own connection this often
_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0, 30.0)

# Queue sentinels for a stream: end it, or re-check who is signed in.
CLOSE = object()
RECHECK = object()


@dataclass(eq=False)
class Subscriber:
    user_id: str
    role: str
    queue: asyncio.Queue
    started: bool = False  # the response began; a subscriber that never starts is dropped by the next subscribe
    behind: bool = False  # the queue overflowed; the next delivery is a full resync instead
    opened_at: float = field(default_factory=time.monotonic)


class LiveHub:
    def __init__(self) -> None:
        self._subscribers: set[Subscriber] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.listening = False
        self.running = False

    # ------------------------------------------------------------ lifecycle

    def start(self, loop: asyncio.AbstractEventLoop, conninfo: str | None) -> bool:
        """Begin listening. `conninfo` is None for a database without LISTEN/NOTIFY (then nothing starts)."""
        if self.running or not conninfo or not change_feed.enabled():
            return False
        self._loop = loop
        self._stop.clear()
        self.running = True
        self._thread = threading.Thread(target=self._listen, args=(conninfo,), name="crm-live-hub", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        self.listening = False
        for subscriber in list(self._subscribers):
            self._put(subscriber, CLOSE)
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=3)

    # ------------------------------------------------------------ subscribers (event-loop thread only)

    def subscribe(self, user_id: str, role: str) -> Subscriber | None:
        """A new stream for the user, or None at the per-user cap (browsers then fall back to polling)."""
        now = time.monotonic()
        for dead in [s for s in self._subscribers if not s.started and now - s.opened_at > 15]:
            self._subscribers.discard(dead)  # the client left before its response began
        if sum(1 for s in self._subscribers if s.user_id == user_id) >= MAX_STREAMS_PER_USER:
            return None
        subscriber = Subscriber(user_id, role, asyncio.Queue(maxsize=QUEUE_LIMIT))
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.discard(subscriber)

    def count(self) -> int:
        return len(self._subscribers)

    def _put(self, subscriber: Subscriber, item) -> None:
        try:
            subscriber.queue.put_nowait(item)
        except asyncio.QueueFull:
            if item is CLOSE:  # make room: a closing stream needs no backlog
                try:
                    while True:
                        subscriber.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                subscriber.queue.put_nowait(CLOSE)
            else:
                subscriber.behind = True

    def deliver(self, payload: dict) -> None:
        """Hand one decoded NOTIFY payload to every stream that may see it. Event-loop thread."""
        if change_feed.is_auth_event(payload):
            try:  # another process may have ended the session or changed the account
                from app.core import deps
                deps._AUTH_CACHE.clear()
            except Exception:
                pass
        for subscriber in list(self._subscribers):
            if change_feed.is_auth_event(payload):
                self._put(subscriber, RECHECK)
            shown = change_feed.visible_to(payload, subscriber.role, subscriber.user_id)
            if shown:
                self._put(subscriber, shown)

    def resync_all(self) -> None:
        for subscriber in list(self._subscribers):
            self._put(subscriber, {"topics": [change_feed.RESYNC_TOPIC], "resync": True, "at": int(time.time() * 1000)})

    # ------------------------------------------------------------ listener thread

    def _post(self, function, *args) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(function, *args)
        except RuntimeError:
            pass

    def _listen(self, conninfo: str) -> None:
        import psycopg

        attempt = 0
        reconnecting = False
        while not self._stop.is_set():
            try:
                with psycopg.connect(conninfo, autocommit=True, connect_timeout=10, keepalives=1, keepalives_idle=30,
                                     keepalives_interval=10, keepalives_count=4) as connection:
                    connection.execute(f"LISTEN {change_feed.CHANNEL}")
                    self.listening = True
                    attempt = 0
                    log.info("Live updates: listening for database changes.")
                    if reconnecting:
                        self._post(self.resync_all)  # anything announced while we were away is lost
                    reconnecting = True
                    last_ping = time.monotonic()
                    while not self._stop.is_set():
                        for notice in connection.notifies(timeout=1.0):
                            self._receive(notice.payload)
                            if self._stop.is_set():
                                break
                        if time.monotonic() - last_ping >= PING_SECONDS:
                            connection.execute("SELECT 1")  # surfaces a silently dropped connection
                            last_ping = time.monotonic()
            except Exception as error:
                self.listening = False
                if self._stop.is_set():
                    break
                reconnecting = True
                delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
                attempt += 1
                log.warning("Live updates: database listener lost (%s); retrying in %.0fs.", type(error).__name__, delay)
                self._stop.wait(delay)
        self.listening = False

    def _receive(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except ValueError:
            return
        if isinstance(payload, dict):
            self._post(self.deliver, payload)


hub = LiveHub()
