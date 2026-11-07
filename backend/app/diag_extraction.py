"""Read-only check of the document reading queue: `python -m app.diag_extraction`.

Prints the newest document jobs (file name, status, attempts, times, lease, last error) and which reading workers have reported in.
It only READS: nothing is changed. It never prints the contents of a document.
"""
from datetime import datetime, timezone

from sqlalchemy import text

from app.db.session import SessionLocal


def _age(moment) -> str:
    if moment is None:
        return "-"
    seconds = int((datetime.now(timezone.utc) - moment).total_seconds())
    return f"{seconds}s ago" if seconds < 120 else f"{seconds // 60} min ago"


def main(sessions=SessionLocal, settings=None) -> None:
    from app.core.config import get_settings
    from app.services.instance_guard import instance_name
    mine = instance_name(settings or get_settings())
    print(f"This installation is '{mine}'. Jobs stamped with another name belong to another installation.")
    with sessions() as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        print("== Newest document jobs ==")
        rows = db.execute(text(
            "SELECT filename, kind, status, attempts, queued_at, started_at, finished_at, COALESCE(ready_at, next_attempt_at) AS due_at, next_attempt_at, claimed_by, lease_until, last_error, "
            "state -> 'failure' AS failure, state -> 'reader' AS reader, instance "
            "FROM document_extraction_jobs ORDER BY created_at DESC LIMIT 8")).all()
        for row in rows:
            print(f"- {row.filename[:40]:40} {row.kind:9} status={row.status:12} attempts={row.attempts} queued={_age(row.queued_at)} "
                  f"started={_age(row.started_at)} finished={_age(row.finished_at)} next_try={_age(row.due_at)} "
                  f"claimed_by={'yes' if row.claimed_by else 'no'} lease={_age(row.lease_until)}")
            if row.instance and row.next_attempt_at.year >= 2100:
                print("    (hidden from older readers: its real due time is kept apart from next_attempt_at)")
            elif row.instance:
                print("    WARNING: stamped, but visible to older readers (run the database update, migration 0032)")
            if row.instance and row.instance != mine:
                print(f"    NOTE: stamped by another installation ('{row.instance}'); this one does not read it.")
            elif row.instance is None:
                print("    (no instance stamp: saved before the stamp existed, or by older code)")
            if row.last_error:
                print(f"    last error: {' '.join(row.last_error.split())[:240]}")
            if row.failure:
                f = row.failure
                print(f"    failure: type={f.get('type')} where={f.get('where')} attempt={f.get('attempt')} at={f.get('at')} "
                      f"worker={f.get('worker')} version={f.get('version')}")
            # A job a current reader handled carries its "reader"; a failed or finished job without one was handled by older
            # code (an old worker still running, here or on another machine that shares this database).
            if row.reader:
                print(f"    handled by reader version={row.reader.get('version')} host={row.reader.get('host')} pid={row.reader.get('pid')}")
            elif row.status in ("failed", "needs_review", "completed", "retry"):
                print("    handled by: OLDER CODE (no reader recorded) - check for an old worker or another deployment on this database")
        if not rows:
            print("(none)")
        print("\n== Reading workers that reported in ==")
        try:
            workers = db.execute(text("SELECT kind, worker_id, seen_at, threads, busy, version FROM crm_worker_heartbeats ORDER BY seen_at DESC LIMIT 10")).all()
            for worker in workers:
                print(f"- {worker.kind:9} seen {_age(worker.seen_at)} threads={worker.threads} busy={worker.busy} version={worker.version}")
            if not workers:
                print("(no worker has reported in yet)")
        except Exception as exc:  # the table only exists after the latest migration
            print(f"(worker table not available: {type(exc).__name__}: run the database update)")


if __name__ == "__main__":
    main()
