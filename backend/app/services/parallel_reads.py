"""Run independent read-only queries at the same time.

The database is often a network hop away (a hosted server costs 150–350 ms per round trip), so a
page that needs six unrelated lookups spends more time waiting in line than reading. Each read gets
its own short-lived session on the caller's engine and they run together; the page then waits for
the slowest one instead of the sum.
"""
from collections.abc import Callable
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy.orm import Session


# Several requests may each fan out at once. Reads queue for a slot, so together they can never ask
# the connection pool for more than this many connections (requests themselves hold one each).
_SLOTS = threading.BoundedSemaphore(10)


def parallel_reads(db, *reads: Callable[[Any], Any]) -> list:
    """Return each read's result, in order. Reads must be side-effect free and return plain values
    or fully loaded rows (the sessions they ran in are closed before this returns)."""
    try:
        engine = db.get_bind()
    except Exception:  # a stand-in session, or one with no bind: run in line
        engine = None
    if engine is None or len(reads) < 2:
        return [read(db) for read in reads]

    def run(read):
        with _SLOTS, Session(engine, autoflush=False, expire_on_commit=False) as session:
            return read(session)

    with ThreadPoolExecutor(max_workers=len(reads)) as pool:
        return list(pool.map(run, reads))
