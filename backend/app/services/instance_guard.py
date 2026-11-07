"""Which installation a queued document belongs to.

Several installations can share one database (a developer's machine and a deployed copy, say) while each keeps its
uploaded files in its own storage. A reading worker must only take jobs whose file it can read. Every new job is stamped
with the name of the installation that saved it (``document_extraction_jobs.instance``); a worker claims jobs stamped
with its own name, or not stamped at all (older rows), and a job whose file is not in its storage is handed back
without counting an attempt, never failed.

The name is ``INSTANCE_NAME`` when set. Otherwise it is the host name plus a short hash of the resolved storage location,
so two machines, or two containers with different storage, never collide.

Limit: code that predates this column ignores it. Until every installation sharing a database runs this code, an old
reader can still claim a job it cannot read. Use a separate database for local work, or update the other installation.
"""
from __future__ import annotations

import hashlib
import socket
from pathlib import Path

NOT_HERE = ("This file is not in this server's storage, so this server cannot read it. "
            "The server that saved it will read it.")


def storage_root(settings) -> str:
    if getattr(settings, "storage_backend", "local") == "s3":
        return f"s3://{getattr(settings, 's3_bucket', '') or ''}/{(getattr(settings, 's3_prefix', '') or '').strip('/')}"
    return str(Path(getattr(settings, "storage_dir", "") or ".").resolve())


def instance_name(settings) -> str:
    configured = (getattr(settings, "instance_name", None) or "").strip()
    if configured:
        return configured[:80]
    digest = hashlib.sha256(storage_root(settings).encode()).hexdigest()[:8]
    return f"{socket.gethostname()[:40] or 'host'}-{digest}"


def stored_here(settings, key: str) -> bool:
    """Whether this server's storage holds the object. An error other than 'not there' counts as there, so that a
    storage outage is retried by the normal attempt budget and is not mistaken for a file that lives elsewhere."""
    from app.services.document_storage import exists
    try:
        return bool(exists(settings, key))
    except Exception:
        return True
