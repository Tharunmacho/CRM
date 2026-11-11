"""Back up the database in backend/.env (the shared one) to artifacts/backups/<name>-<time>/ before any data change.

    python scripts/backup_shared_database.py --name before-travelled-import

Needs Docker Desktop running (it uses the postgres:18-alpine client, because the server is PostgreSQL 18 and an older pg_dump
refuses it). The connection string is read from backend/.env and is never printed. The dump is read back with pg_restore --list,
so a file that is empty or damaged is reported instead of trusted. Only database rows are saved: uploaded document files are in
the upload volume and need their own copy.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="backup")
    args = parser.parse_args()
    match = re.search(r"^DATABASE_URL=(.+)$", (ROOT / "backend" / ".env").read_text(encoding="utf-8"), re.M)
    if not match:
        print("DATABASE_URL was not found in backend/.env", file=sys.stderr)
        return 1
    parts = urlsplit(match.group(1).strip().strip('"').replace("postgresql+psycopg://", "postgresql://"))
    if parts.hostname in {"127.0.0.1", "localhost"}:      # the container reaches the tunnel on this computer through the host alias
        netloc = parts.netloc.replace(parts.hostname, "host.docker.internal")
        parts = parts._replace(netloc=netloc)
    url = urlunsplit(parts)
    out = ROOT / "artifacts" / "backups" / f"{args.name}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    print(f"Saving to {out} ...")
    dump = subprocess.run(["docker", "run", "--rm", "-v", f"{out}:/out", "postgres:18-alpine", "pg_dump", "-Fc", "-f", "/out/database.dump", url],
                          capture_output=True, text=True)
    if dump.returncode != 0:
        print("pg_dump failed:", dump.stderr.replace(url, "<connection>")[-600:], file=sys.stderr)
        return 1
    check = subprocess.run(["docker", "run", "--rm", "-v", f"{out}:/out", "postgres:18-alpine", "pg_restore", "--list", "/out/database.dump"],
                           capture_output=True, text=True)
    entries = [line for line in check.stdout.splitlines() if line and not line.startswith(";")]
    size = (out / "database.dump").stat().st_size
    if check.returncode != 0 or not entries or size < 10_000:
        print(f"The dump could not be verified (size {size} bytes, {len(entries)} entries).", file=sys.stderr)
        return 1
    digest = hashlib.sha256((out / "database.dump").read_bytes()).hexdigest()
    print(f"OK: {size:,} bytes, {len(entries)} objects readable, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
