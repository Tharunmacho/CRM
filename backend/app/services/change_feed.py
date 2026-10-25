"""Capture which business data a committed transaction changed, and announce it to every open desk.

Real-time push has three parts; this module is the first and the pure-logic part of the rest:

  1. Capture (here). Every INSERT/UPDATE/DELETE the process runs is noted against its connection, by table, and
     the ORM adds entity ids when it flushed objects. Just before the transaction COMMITs, one
     `pg_notify('crm_changes', <small json>)` is sent on the same connection. PostgreSQL delivers a NOTIFY only if
     the transaction commits, so rolled-back work never reaches anyone and a committed write is never missed.
  2. Fan-out (`live_hub.py`): one LISTEN connection per process hands the payloads to open event streams.
  3. Delivery (`api/routers/events.py`): GET /api/events, a Server-Sent Events stream filtered per user.

Why statements and not only `Session.after_flush`: much of this code base writes with Core statements
(`db.execute(delete(Company)...)`, `update(Notification)...`), which never create ORM flush events. Watching the
statement stream catches every write path, including raw SQL and workers, with no change to any caller. The ORM
`after_flush` hook still runs because only it knows entity ids.

A payload carries topic names and opaque ids only (never names, numbers, e-mail text or any personal data):
    {"t": ["candidates", "tasks", "reports"], "ids": {"candidates": ["<uuid>"]}, "u": {"todos": ["<user-id>"]}, "at": 1730000000000}

Postgres only; on any other dialect (the unit tests run on SQLite) every hook returns at once.
Turn the whole feature off with CRM_LIVE_EVENTS=0.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache

from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import Pool

log = logging.getLogger("uvicorn.error")

CHANNEL = "crm_changes"
MAX_PAYLOAD_BYTES = 7000  # PostgreSQL refuses a NOTIFY payload of 8000 bytes or more
MAX_IDS_PER_TOPIC = 25
_KEY = "crm_change_feed"

# Control topic: a sign-in session or account was removed or changed. Never sent to browsers; the event
# streams use it to re-check their user at once (force-logout, deactivation, role change).
AUTH_TOPIC = "_auth"
# Everything may have changed (the listener reconnected, or a subscriber fell behind): refetch the lot.
RESYNC_TOPIC = "*"

# Table -> the client-facing topics it feeds. An allow-list: a table that is not here (and not in IGNORED) is
# reported by a test, so a new table forces a decision instead of silently never pushing.
TOPIC_TABLES: dict[str, tuple[str, ...]] = {
    "crm_candidates": ("candidates",),
    "document_attachments": ("candidates", "documents"),
    "crm_applications": ("applications",),
    "crm_application_stages": ("applications",),
    "workflow_tasks": ("tasks",),
    "workflow_task_remarks": ("tasks",),
    "workflow_task_events": ("tasks",),
    "workflow_settings": ("tasks",),
    "crm_client_contacts": ("clients",),
    "crm_employer_companies": ("companies",),
    "crm_job_orders": ("orders",),
    "crm_job_designations": ("designations",),
    "system_notifications": ("notifications",),
    "personal_todos": ("todos",),
    "auth_users": ("users",),
    "staff_attendance_sessions": ("attendance",),
    "staff_attendance_requests": ("attendance",),
    "staff_presence_intervals": ("attendance",),
    "staff_attendance_hr_requests": ("attendance",),
    "staff_attendance_weekly_off": ("attendance",),
    "staff_attendance_work_timings": ("attendance",),
    "staff_attendance_duty_plans": ("attendance",),
    "staff_attendance_calendar": ("attendance",),
    "staff_attendance_adjustments": ("attendance",),
    "staff_attendance_cover_items": ("attendance",),
    "email_accounts": ("mail",),
    "email_messages": ("mail",),
    "email_attachments": ("mail",),
    "email_replies": ("mail",),
    "email_drafts": ("mail",),
    "email_draft_attachments": ("mail",),
    "email_send_attempts": ("mail",),
    "email_ingestion_jobs": ("mail", "documents"),
    "intake_acknowledgement_settings": ("mail", "whatsapp"),
    "intake_acknowledgements": ("mail", "whatsapp"),
    "whatsapp_channels": ("whatsapp",),
    "whatsapp_messages": ("whatsapp", "documents"),
    "whatsapp_conversations": ("whatsapp",),
    "whatsapp_send_attempts": ("whatsapp",),
    "whatsapp_delivery_events": ("whatsapp",),
    "whatsapp_templates": ("whatsapp",),
    "document_extraction_jobs": ("documents",),
    "document_person_intakes": ("documents",),
    "document_person_groups": ("documents",),
    "ingestion_failures": ("documents",),
    "agency_settings": ("settings",),
    "currencies": ("settings",),
}

# Internal bookkeeping that no screen shows: writing to it announces nothing.
IGNORED_TABLES = frozenset({
    "system_audit_log",           # the audit trail is written beside every change it records
    "workflow_assignment_cursor",  # round-robin cursor
    "staff_presence_clients",     # per-page heartbeat ordering checkpoint
    "email_oauth_states",         # short-lived consent handshakes
})

# Columns that workers rewrite continuously (heartbeats, retry clocks, OCR checkpoints). An UPDATE that sets only
# these is not news; it would wake every desk every few seconds for nothing.
NOISE_COLUMNS = frozenset({
    "updated_at", "worker_seen_at", "extraction_seen_at", "last_seen_at", "last_sync_at", "sync_state",
    "attempts", "next_attempt_at", "state", "last_run_at", "last_daily_check_date", "last_received_at",
})

# A change to any of these also stales the dashboard and report numbers built from them.
REPORT_SOURCES = frozenset({
    "candidates", "applications", "tasks", "clients", "companies", "orders", "designations", "attendance",
    "mail", "whatsapp", "documents", "users",
})
# Personal topics: only the owner is told when the writer named the owner (otherwise everyone is, harmlessly).
OWNER_COLUMN = {"personal_todos": ("todos", "owner_id"), "system_notifications": ("notifications", "user_id")}
OWNER_TOPICS = frozenset({"todos", "notifications"})

# Everything a browser may be told about. The two control topics are deliberately missing.
CLIENT_TOPICS = frozenset({
    "candidates", "applications", "tasks", "clients", "companies", "orders", "designations", "notifications",
    "todos", "users", "attendance", "mail", "whatsapp", "documents", "reports", "settings",
})
# Only supervisors get entity ids; a recruiter's stream carries topic names alone.
ID_ROLES = frozenset({"admin", "manager"})
# Candidate ids touched through related tables, so an open profile knows its candidate moved.
_CANDIDATE_LINKS = {"crm_applications": "candidate_id", "workflow_tasks": "candidate_id"}


def enabled() -> bool:
    return os.environ.get("CRM_LIVE_EVENTS", "1").strip().lower() not in {"0", "false", "off", "no"}


# ------------------------------------------------------------------ statement parsing

_DML = re.compile(r'^\s*(INSERT\s+INTO|UPDATE(?:\s+ONLY)?|DELETE\s+FROM)\s+(?:"?public"?\.)?"?([A-Za-z_][A-Za-z0-9_]*)"?', re.I)
_SET = re.compile(r"\bSET\s+(.*?)(?:\s+(?:FROM|WHERE|RETURNING)\b|;?\s*$)", re.I | re.S)
_COLUMN = re.compile(r'(?:^|,)\s*"?([A-Za-z_][A-Za-z0-9_]*)"?\s*=')


@lru_cache(maxsize=1024)
def classify_statement(statement: str) -> tuple[str, str] | None:
    """('INSERT'|'UPDATE'|'DELETE', table) for a write that announces something, else None."""
    found = _DML.match(statement)
    if not found:
        return None
    verb = found.group(1).split()[0].upper()
    table = found.group(2)
    if table in IGNORED_TABLES:
        return None
    if verb == "UPDATE":
        assigned = _SET.search(statement)
        columns = set(_COLUMN.findall(assigned.group(1))) if assigned else set()
        if columns and columns <= NOISE_COLUMNS:
            return None
    if table in ("auth_sessions", "auth_users"):
        # Signing in only adds a session; removing or changing one can end someone's access.
        return (verb, table) if (table == "auth_users" or verb != "INSERT") else None
    if table not in TOPIC_TABLES:
        return None
    return verb, table


# ------------------------------------------------------------------ what one transaction changed

class Pending:
    """Changes noted on one connection since its last COMMIT/ROLLBACK."""

    __slots__ = ("topics", "ids", "owners", "owner_known", "candidates", "auth")

    def __init__(self) -> None:
        self.topics: set[str] = set()
        self.ids: dict[str, set[str]] = {}
        self.owners: dict[str, set[str]] = {}
        self.owner_known: set[str] = set()
        self.candidates: set[str] = set()
        self.auth = False

    def note_table(self, verb: str, table: str) -> None:
        if table == "auth_sessions":
            self.auth = True
            return
        if table == "auth_users" and verb != "INSERT":
            self.auth = True
        self.topics.update(TOPIC_TABLES.get(table, ()))

    def note_object(self, table: str, row_id: str | None, owner: str | None, candidate_id: str | None) -> None:
        topics = TOPIC_TABLES.get(table, ())
        for topic in topics:
            if row_id:
                self.ids.setdefault(topic, set()).add(row_id)
        scoped = OWNER_COLUMN.get(table)
        if scoped:
            self.owner_known.add(scoped[0])
            if owner:
                self.owners.setdefault(scoped[0], set()).add(owner)
        if candidate_id:
            self.candidates.add(candidate_id)

    def payload(self, now_ms: int | None = None) -> str | None:
        topics = set(self.topics)
        if not topics and not self.auth:
            return None
        if topics & REPORT_SOURCES:
            topics.add("reports")
        if self.auth:
            topics.add(AUTH_TOPIC)
        body: dict = {"t": sorted(topics), "at": now_ms if now_ms is not None else int(time.time() * 1000)}
        ids = {topic: sorted(found) for topic, found in self.ids.items() if topic in topics and 0 < len(found) <= MAX_IDS_PER_TOPIC}
        if self.candidates and len(self.candidates) <= MAX_IDS_PER_TOPIC:
            ids["candidates"] = sorted(set(ids.get("candidates", [])) | self.candidates)
        owners = {topic: sorted(found) for topic, found in self.owners.items()
                  if topic in topics and topic in self.owner_known and 0 < len(found) <= MAX_IDS_PER_TOPIC}
        if ids:
            body["ids"] = ids
        if owners:
            body["u"] = owners
        text = json.dumps(body, separators=(",", ":"))
        if len(text.encode()) >= MAX_PAYLOAD_BYTES:  # unreachable with the caps above; kept as a hard guarantee
            body.pop("ids", None)
            body.pop("u", None)
            text = json.dumps(body, separators=(",", ":"))
        return text


def _pending(info: dict) -> Pending:
    pending = info.get(_KEY)
    if pending is None:
        pending = info[_KEY] = Pending()
    return pending


# ------------------------------------------------------------------ what a given user may be told

def visible_to(payload: dict, role: str, user_id: str) -> dict | None:
    """The part of a NOTIFY payload this user may receive, in the shape the browser gets; None when nothing.

    - Personal topics (to-dos, notifications) go only to the owner when the writer named one.
    - Entity ids are for admin/manager; a recruiter's event is topic names only.
    - `users` goes to every role because every role's workspace shows colleagues' names (owners, assignees);
      withholding it would leave a renamed or added colleague stale for staff. It carries no data.
    - Control topics never leave the server.
    """
    owners = payload.get("u") or {}
    topics: list[str] = []
    for topic in payload.get("t") or ():
        if topic not in CLIENT_TOPICS:
            continue
        if topic in OWNER_TOPICS and topic in owners and user_id not in owners[topic]:
            continue
        topics.append(topic)
    if not topics:
        return None
    event_body: dict = {"topics": topics, "at": payload.get("at")}
    if role in ID_ROLES and payload.get("ids"):
        shown = {topic: found for topic, found in payload["ids"].items() if topic in topics}
        if shown:
            event_body["ids"] = shown
    return event_body


def merge_events(first: dict, second: dict) -> dict:
    """Combine two client-facing events into one (used when several arrive within one coalescing window)."""
    topics = list(dict.fromkeys([*first.get("topics", ()), *second.get("topics", ())]))
    merged: dict = {"topics": topics, "at": max(first.get("at") or 0, second.get("at") or 0) or None}
    if first.get("resync") or second.get("resync"):
        merged["resync"] = True
    ids: dict[str, list[str]] = {}
    for source in (first.get("ids") or {}, second.get("ids") or {}):
        for topic, found in source.items():
            ids[topic] = sorted(set(ids.get(topic, [])) | set(found))[: MAX_IDS_PER_TOPIC * 2]
    if ids and not merged.get("resync"):
        merged["ids"] = ids
    return merged


def is_auth_event(payload: dict) -> bool:
    return AUTH_TOPIC in (payload.get("t") or ())


# ------------------------------------------------------------------ hooks

def _postgres(conn) -> bool:
    return conn.dialect.name == "postgresql"


def _statement_hook(conn, cursor, statement, parameters, context, executemany):
    if statement.lstrip()[:6].upper() not in ("INSERT", "UPDATE", "DELETE"):
        return
    if not _postgres(conn):
        return
    found = classify_statement(statement)
    if found:
        _pending(conn.info).note_table(*found)


def _commit_hook(conn):
    pending = conn.info.pop(_KEY, None)
    if pending is None:
        return
    payload = pending.payload()
    if payload is None:
        return
    try:
        # The raw DBAPI cursor: no events, no statement log, and the NOTIFY joins this very transaction.
        with conn.connection.cursor() as cursor:
            cursor.execute("SELECT pg_notify(%s::text, %s::text)", (CHANNEL, payload))
    except Exception as error:  # never fail a save because the announcement could not be queued
        log.warning("Live change notification skipped (%s).", type(error).__name__)


def _rollback_hook(conn):
    conn.info.pop(_KEY, None)


def _checkout_hook(dbapi_connection, connection_record, connection_proxy):
    connection_record.info.pop(_KEY, None)  # nothing carried over from a previous borrower


def _table_of(obj) -> str | None:
    table = getattr(obj, "__table__", None)
    return getattr(table, "name", None)


def _flush_hook(session: Session, flush_context) -> None:
    """Add entity ids (and the owner of personal rows) for ORM objects that were just flushed."""
    try:
        bind = session.get_bind()
        if bind.dialect.name != "postgresql":
            return
        pending = _pending(session.connection().info)
        for objects, changed_only in ((session.new, False), (session.dirty, True), (session.deleted, False)):
            for obj in objects:
                table = _table_of(obj)
                if table not in TOPIC_TABLES:
                    continue
                if changed_only:
                    state = inspect(obj)
                    touched = set(state.committed_state)
                    if touched and touched <= NOISE_COLUMNS:
                        continue
                owner = getattr(obj, OWNER_COLUMN[table][1], None) if table in OWNER_COLUMN else None
                link = _CANDIDATE_LINKS.get(table)
                candidate_id = getattr(obj, link, None) if link else (getattr(obj, "id", None) if table == "crm_candidates" else None)
                pending.note_object(table, getattr(obj, "id", None), owner, candidate_id)
                pending.topics.update(TOPIC_TABLES[table])
    except Exception as error:  # ids are an optimisation; the statement hook already recorded the topics
        log.debug("Change feed could not read flushed objects (%s).", type(error).__name__)


_installed = False


def install() -> None:
    """Register the hooks once. Safe to call repeatedly; does nothing when CRM_LIVE_EVENTS=0."""
    global _installed
    if _installed or not enabled():
        return
    _installed = True
    event.listen(Engine, "before_cursor_execute", _statement_hook)
    event.listen(Engine, "commit", _commit_hook)
    event.listen(Engine, "rollback", _rollback_hook)
    event.listen(Pool, "checkout", _checkout_hook)
    event.listen(Session, "after_flush", _flush_hook)
