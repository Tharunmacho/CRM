"""A savepoint is a safety net for a statement that might fail because a table or column is missing (a database that
has not been migrated yet). It costs two extra round trips (SAVEPOINT, RELEASE), and on the hosted database each
round trip is about 300 ms. Once the same guarded block has succeeded in this process, the table or column is there
for good (migrations only add), so later calls run the statement directly: one round trip instead of three."""

from contextlib import contextmanager

_proven: set[str] = set()


@contextmanager
def guarded(db, name: str):
    """`with guarded(db, "x"):` wraps the block in a savepoint until it has completed once; `db` is a Session or a Connection."""
    if name in _proven:
        yield
        return
    with db.begin_nested():
        yield
    _proven.add(name)


def forget() -> None:
    """For tests: the next call of every block is guarded again."""
    _proven.clear()
