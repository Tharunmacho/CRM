"""Veris OCR usage report: what the CRM sent to the paid Veris provider, for billing the client by volume.

    python scripts/veris_usage_report.py                                  # everything, default settings database
    python scripts/veris_usage_report.py --from 2026-01-01 --to 2026-03-31 --format csv
    python scripts/veris_usage_report.py --database-url postgresql+psycopg://user:pw@host:5432/crm

PURELY READ ONLY. The session is opened with `SET TRANSACTION READ ONLY` (and the connection with
`default_transaction_read_only=on`), and a guard refuses to execute any statement that is not a SELECT. The script never calls
Veris and never prints a password. The database host and name are printed before anything is read.

What is read: the durable OCR checkpoints the CRM keeps for every document it read, in four places that all share one shape
(`provider_jobs`, `provider_job_history`, `coverage`, `inspection`):
  * document_extraction_jobs.state            manual uploads, lead resume checks, application-form documents, bulk import
  * email_ingestion_jobs.state[<attachment>]  documents that arrived by email
  * whatsapp_messages.processing_state        documents that arrived by WhatsApp
  * document_person_intakes.sources[0].group_extractions[<group>].state   pages re-read per person of a multi-person file
Each Veris job is one `jobs.submit` and has one `job_id`; a long document is several jobs (chunks), a passport of several pages is
one job per page. A job id is counted ONCE across all sources (a reused or copied checkpoint repeats the same ids).

Only OUR candidates are in the totals: a document counts when it resolves to a row of crm_candidates (see the Notes sheet).
Everything else is listed on a separate "Excluded - not linked" sheet, without personal data. No document text, phone number,
e-mail or passport number is ever written; file names are scrubbed of long digit runs and e-mail addresses.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, event, select  # noqa: E402
from sqlalchemy.engine import Connection, make_url  # noqa: E402

from app.db.models import (  # noqa: E402
    Application, ApplicationStatus, Attachment, Candidate, DocumentExtractionJob, DocumentPersonGroup, EmailIngestionJob,
    MailAttachment, MailMessage, MultiPersonIntake, User, WhatsAppMessage,
)

DEFAULT_OUT_DIR = r"M:\crm\reports\veris-usage"
MODES = ("resume", "passport", "aadhaar")
KIND_ROWS = (*MODES, "other")
KIND_LABEL = {"resume": "Resume", "passport": "Passport", "aadhaar": "Aadhaar", "other": "Other / unclassified"}
TRAVELLED, CURRENT = "Travelled (deployed)", "Current"
EXCLUDED_SHEET = "Excluded - not linked"
EXCLUDED_TITLE = "Excluded - not linked to a candidate"
SOURCE_LABEL = {"upload": "Upload / form / import", "email": "Email", "whatsapp": "WhatsApp", "split": "Multi-person review"}
_ALLOWED_SQL = ("select", "with", "set transaction read only", "show")


# ----------------------------------------------------------------------------- read-only connection

class WriteRefused(RuntimeError):
    pass


def read_only_engine(url: str):
    """An engine whose every connection is read only at the server and whose statements are checked before they are sent."""
    parsed = make_url(url)
    args = {}
    if parsed.get_backend_name() == "postgresql" and parsed.drivername.endswith("psycopg"):
        args["options"] = "-c default_transaction_read_only=on"
    engine = create_engine(url, connect_args=args, pool_pre_ping=False)

    @event.listens_for(engine, "before_cursor_execute")
    def _guard(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        if not statement.lstrip().lower().startswith(_ALLOWED_SQL):
            raise WriteRefused("Refused to run a non-SELECT statement: " + statement.lstrip()[:30])

    return engine


def describe(url: str) -> str:
    parsed = make_url(url)
    return f"{parsed.host or 'local socket'}:{parsed.port or ''}/{parsed.database}".replace(":/", "/")


def begin_read_only(conn: Connection) -> None:
    conn.exec_driver_sql("SET TRANSACTION READ ONLY")


# ----------------------------------------------------------------------------- reading the checkpoints

@dataclass
class Job:
    job_id: str
    mode: str
    pages: list[int]
    status: str
    retry_generation: int
    key: str
    superseded: bool          # an earlier attempt kept in provider_job_history
    duplicate: bool           # the reader said it already had a job under this idempotency key
    synthetic_id: bool = False
    new: bool = True          # False when the same job id was already counted for another document


@dataclass
class Unit:
    """One document the CRM read (a row of one of the four sources)."""
    source: str
    unit_id: str
    filename: str
    kind: str                 # the uploaded type (resume / passport / aadhaar / other)
    content_hash: str
    created_at: datetime
    status: str
    owner_id: str | None
    candidate_id: str | None = None
    link: str = ""            # how the candidate was found
    pages_total: int | None = None
    ignored: int = 0
    unresolved: int = 0
    inspected: bool = False
    jobs: list[Job] = field(default_factory=list)
    candidate: dict | None = None
    excluded: str = ""        # reason, empty when counted
    note: str = ""

    @property
    def new_jobs(self) -> list[Job]:
        return [job for job in self.jobs if job.new]

    @property
    def sent_pages(self) -> set[int]:
        return {page for job in self.jobs for page in job.pages}


def _mode(kind: str) -> str:
    return str(kind).rstrip("+")


def _pages(value) -> list[int]:
    return [int(p) for p in value if isinstance(p, int) or (isinstance(p, str) and p.isdigit())] if isinstance(value, list) else []


def _leaves(record) -> list[dict]:
    if not isinstance(record, dict):
        return []
    found = [chunk for chunk in record.get("chunks") or [] if isinstance(chunk, dict) and (chunk.get("job_id") or "result" in chunk)]
    if record.get("job_id") or ("result" in record and not record.get("chunks")):
        found.append(record)
    return found


def _job_from(unit_key: str, mode: str, leaf: dict, superseded: bool) -> Job:
    job_id = leaf.get("job_id")
    pages = _pages(leaf.get("selected_pages"))
    key = str(leaf.get("idempotency_key") or "")
    return Job(job_id=str(job_id) if job_id else f"nojobid:{unit_key}:{mode}:{key or pages}", mode=mode, pages=pages,
               status=str(leaf.get("status") or ""), retry_generation=int(leaf.get("retry_generation") or 0), key=key,
               superseded=superseded, duplicate=bool(leaf.get("duplicate")), synthetic_id=not job_id)


def read_state(unit: Unit, provider_jobs, history, coverage_pages, inspection_pages, has_inspection=False) -> None:
    """Fill the unit's jobs and page figures from the four saved fragments of one checkpoint."""
    seen: set[str] = set()
    for kind, record in (provider_jobs or {}).items() if isinstance(provider_jobs, dict) else ():
        for leaf in _leaves(record):
            job = _job_from(unit.unit_id, _mode(kind), leaf, False)
            if job.job_id not in seen:
                seen.add(job.job_id)
                unit.jobs.append(job)
    for kind, records in (history or {}).items() if isinstance(history, dict) else ():
        for record in records if isinstance(records, list) else ():
            for leaf in _leaves(record):
                job = _job_from(unit.unit_id, _mode(kind), leaf, True)
                if job.job_id not in seen:
                    seen.add(job.job_id)
                    unit.jobs.append(job)
    coverage_pages = coverage_pages if isinstance(coverage_pages, dict) else {}
    total = coverage_pages.get("total", inspection_pages)
    unit.pages_total = int(total) if isinstance(total, (int, float)) and total > 0 else None
    unit.ignored = len(coverage_pages.get("ignored") or [])
    unit.unresolved = len(coverage_pages.get("unresolved") or [])
    unit.inspected = bool(has_inspection or coverage_pages or inspection_pages)


# ----------------------------------------------------------------------------- collecting

def _scrub(name: str | None) -> str:
    """The file name without anything that could identify a person: no path, e-mail, phone or document numbers."""
    text = Path(str(name or "").replace("\\", "/")).name
    text = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", text)
    text = re.sub(r"\+?\d[\d\s-]{6,}\d", "[number]", text)
    text = re.sub(r"[A-Za-z]{0,2}\d{6,}", "[id]", text)
    return text[:150]


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _kind(value: str | None) -> str:
    value = (value or "").lower()
    return value if value in MODES else "other"


def _chunks(items, size=500):
    items = list(items)
    for index in range(0, len(items), size):
        yield items[index:index + size]


def collect(conn: Connection, date_from: date | None = None, date_to: date | None = None) -> dict:
    """Every document unit in the date range with its Veris jobs, resolved to a candidate (or excluded). Read only."""
    lower = datetime.combine(date_from, datetime.min.time(), tzinfo=timezone.utc) if date_from else None
    upper = datetime.combine(date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) if date_to else None

    def in_range(column, statement):
        if lower is not None:
            statement = statement.where(column >= lower)
        if upper is not None:
            statement = statement.where(column < upper)
        return statement

    users = {row.id: row.name for row in conn.execute(select(User.id, User.name))}
    applications = {row[0] for row in conn.execute(select(Application.candidate_id).where(Application.status == ApplicationStatus.deployed))}
    candidates = {}
    for row in conn.execute(select(Candidate.id, Candidate.ref, Candidate.care_id, Candidate.traveller_id, Candidate.name,
                                   Candidate.status, Candidate.owner_id, Candidate.intake_owner_id)):
        status = getattr(row.status, "value", row.status)
        candidates[row.id] = {"name": row.name, "ref": row.ref, "care_id": row.care_id or "", "traveller_id": row.traveller_id or "",
                              "cohort": TRAVELLED if (status == "deployed" or row.id in applications or row.traveller_id) else CURRENT,
                              "recruiter": row.intake_owner_id or row.owner_id}
    # Links that do not depend on the date range: a file's group may have been linked by a later upload.
    group_candidate: dict[str, str] = {}
    for group_id, candidate_id in conn.execute(select(DocumentExtractionJob.group_id, DocumentExtractionJob.candidate_id)
                                               .where(DocumentExtractionJob.candidate_id.is_not(None))):
        group_candidate.setdefault(group_id, candidate_id)
    bundle_candidates: dict[str, list[str]] = defaultdict(list)
    for source_id, candidate_id in conn.execute(
            select(MultiPersonIntake.source_id, DocumentPersonGroup.candidate_id)
            .join(DocumentPersonGroup, DocumentPersonGroup.bundle_id == MultiPersonIntake.id)
            .where(MultiPersonIntake.source_type == "manual", DocumentPersonGroup.candidate_id.is_not(None))
            .order_by(MultiPersonIntake.id, DocumentPersonGroup.ordinal)):
        if candidate_id not in bundle_candidates[source_id]:
            bundle_candidates[source_id].append(candidate_id)
    attachment_candidate = {row.id: row.entity_id for row in conn.execute(
        select(Attachment.id, Attachment.entity_id).where(Attachment.entity_type == "candidate"))}

    units: list[Unit] = []
    counts = {"rows_read": defaultdict(int), "empty_state": 0, "no_job_id": 0}

    # 1. manual uploads and everything built on them
    statement = in_range(DocumentExtractionJob.created_at, select(
        DocumentExtractionJob.id, DocumentExtractionJob.kind, DocumentExtractionJob.filename, DocumentExtractionJob.content_hash,
        DocumentExtractionJob.status, DocumentExtractionJob.owner_id, DocumentExtractionJob.group_id, DocumentExtractionJob.candidate_id,
        DocumentExtractionJob.attachment_id, DocumentExtractionJob.created_at,
        DocumentExtractionJob.state["provider_jobs"].label("jobs"), DocumentExtractionJob.state["provider_job_history"].label("history"),
        DocumentExtractionJob.state["coverage"]["pages"].label("coverage"), DocumentExtractionJob.state["inspection"]["page_count"].label("pages"),
        DocumentExtractionJob.state["inspection"].label("inspection")))
    for row in conn.execution_options(stream_results=True).execute(statement.order_by(DocumentExtractionJob.created_at, DocumentExtractionJob.id)):
        counts["rows_read"]["upload"] += 1
        unit = Unit("upload", row.id, _scrub(row.filename), _kind(row.kind), row.content_hash or "", _utc(row.created_at), row.status, row.owner_id)
        if row.candidate_id:
            unit.candidate_id, unit.link = row.candidate_id, "candidate_id"
        elif attachment_candidate.get(row.attachment_id or ""):
            unit.candidate_id, unit.link = attachment_candidate[row.attachment_id], "attachment"
        elif group_candidate.get(row.group_id):
            unit.candidate_id, unit.link = group_candidate[row.group_id], "same upload group"
        elif bundle_candidates.get(row.group_id):
            unit.candidate_id, unit.link = bundle_candidates[row.group_id][0], "multi-person review"
            if len(bundle_candidates[row.group_id]) > 1:
                unit.note = f"file shared by {len(bundle_candidates[row.group_id])} candidates (first one shown)"
        read_state(unit, row.jobs, row.history, row.coverage, row.pages, bool(row.inspection))
        if not (row.jobs or row.history or row.coverage or row.inspection):
            counts["empty_state"] += 1
        if row.status == "dismissed":
            unit.excluded = "dismissed"
        units.append(unit)

    # 2. documents that arrived by email (one checkpoint per attachment inside the job's state)
    statement = in_range(EmailIngestionJob.created_at, select(
        EmailIngestionJob.id, EmailIngestionJob.message_id, EmailIngestionJob.status, EmailIngestionJob.candidate_id,
        EmailIngestionJob.created_at, EmailIngestionJob.state))
    mail_rows = list(conn.execute(statement.order_by(EmailIngestionJob.created_at, EmailIngestionJob.id)))
    attachment_ids = [aid for row in mail_rows if isinstance(row.state, dict) for aid, value in row.state.items() if isinstance(value, dict)]
    attachments = {}
    for batch in _chunks(attachment_ids):
        for row in conn.execute(select(MailAttachment.id, MailAttachment.file_name, MailAttachment.content_hash, MailAttachment.kind)
                                .where(MailAttachment.id.in_(batch))):
            attachments[row.id] = row
    for row in mail_rows:
        counts["rows_read"]["email"] += 1
        if not isinstance(row.state, dict) or not row.state:
            counts["empty_state"] += 1
            continue
        for aid, value in row.state.items():
            if not isinstance(value, dict):
                continue
            attachment = attachments.get(aid)
            unit = Unit("email", f"{row.id}/{aid}", _scrub(attachment.file_name if attachment else ""), _kind(attachment.kind if attachment else None),
                        (attachment.content_hash if attachment else value.get("source_sha256")) or "", _utc(row.created_at), row.status, None,
                        candidate_id=row.candidate_id, link="email job" if row.candidate_id else "")
            read_state(unit, value.get("provider_jobs"), value.get("provider_job_history"), (value.get("coverage") or {}).get("pages"),
                       (value.get("inspection") or {}).get("page_count"), bool(value.get("inspection")))
            units.append(unit)

    # 3. documents that arrived by WhatsApp (the whole processing_state is the checkpoint)
    statement = in_range(WhatsAppMessage.received_at, select(
        WhatsAppMessage.id, WhatsAppMessage.file_name, WhatsAppMessage.content_hash, WhatsAppMessage.status, WhatsAppMessage.candidate_id,
        WhatsAppMessage.received_at, WhatsAppMessage.processing_state["provider_jobs"].label("jobs"),
        WhatsAppMessage.processing_state["provider_job_history"].label("history"),
        WhatsAppMessage.processing_state["coverage"]["pages"].label("coverage"),
        WhatsAppMessage.processing_state["inspection"]["page_count"].label("pages"),
        WhatsAppMessage.processing_state["inspection"].label("inspection")))
    for row in conn.execute(statement.order_by(WhatsAppMessage.received_at, WhatsAppMessage.id)):
        counts["rows_read"]["whatsapp"] += 1
        if not (row.jobs or row.history or row.coverage or row.inspection):
            counts["empty_state"] += 1
            continue
        unit = Unit("whatsapp", row.id, _scrub(row.file_name), "other", row.content_hash or "", _utc(row.received_at), row.status, None,
                    candidate_id=row.candidate_id, link="whatsapp message" if row.candidate_id else "")
        read_state(unit, row.jobs, row.history, row.coverage, row.pages, bool(row.inspection))
        units.append(unit)

    # 4. pages re-read per person of a multi-person file
    statement = in_range(MultiPersonIntake.created_at, select(MultiPersonIntake.id, MultiPersonIntake.status, MultiPersonIntake.created_at,
                                                              MultiPersonIntake.sources))
    bundles = list(conn.execute(statement.order_by(MultiPersonIntake.created_at, MultiPersonIntake.id)))
    group_rows: dict[str, dict[str, str | None]] = defaultdict(dict)
    for batch in _chunks([row.id for row in bundles]):
        for group in conn.execute(select(DocumentPersonGroup.id, DocumentPersonGroup.bundle_id, DocumentPersonGroup.candidate_id)
                                  .where(DocumentPersonGroup.bundle_id.in_(batch))):
            group_rows[group.bundle_id][group.id] = group.candidate_id
    for row in bundles:
        counts["rows_read"]["split"] += 1
        source = (row.sources or [{}])[0] if isinstance(row.sources, list) and row.sources else {}
        extractions = source.get("group_extractions") if isinstance(source, dict) else None
        if not isinstance(extractions, dict):
            continue
        for group_id, record in extractions.items():
            state = (record or {}).get("state") or {}
            if not isinstance(state, dict) or not (state.get("provider_jobs") or state.get("provider_job_history")):
                continue
            status = "failed" if record.get("error") else "completed" if record.get("finished") else "pending"
            unit = Unit("split", f"{row.id}/{group_id}", _scrub(source.get("name")), "other", str(source.get("hash") or ""), _utc(row.created_at),
                        status, None, candidate_id=group_rows[row.id].get(group_id), link="person group" if group_rows[row.id].get(group_id) else "")
            read_state(unit, state.get("provider_jobs"), state.get("provider_job_history"), (state.get("coverage") or {}).get("pages"),
                       (state.get("inspection") or {}).get("page_count"), bool(state.get("inspection")))
            units.append(unit)

    # ---- resolve candidates, scope, recruiter
    for unit in units:
        found = candidates.get(unit.candidate_id or "")
        unit.candidate = found
        if unit.candidate_id and not found:
            unit.candidate_id, unit.link = None, ""
        if not unit.excluded and not found:
            unit.excluded = "not linked to a candidate"
        if unit.kind == "other":
            modes = {job.mode for job in unit.jobs}
            if len(modes) == 1 and unit.source in {"email", "whatsapp", "split"}:
                unit.kind = next(iter(modes))
    # ---- count each Veris job id once: documents of our candidates claim first, then the earliest
    claimed: set[str] = set()
    for unit in sorted(units, key=lambda u: (bool(u.excluded), u.created_at, u.unit_id)):
        for job in unit.jobs:
            job.new = job.synthetic_id or job.job_id not in claimed
            claimed.add(job.job_id)
    counts["no_job_id"] = sum(1 for unit in units for job in unit.jobs if job.synthetic_id)
    return {"units": units, "users": users, "counts": counts, "date_from": date_from, "date_to": date_to}


# ----------------------------------------------------------------------------- numbers

def _recruiter(unit: Unit, users: dict) -> str:
    owner = unit.owner_id or (unit.candidate or {}).get("recruiter")
    return users.get(owner, "(unknown)") if owner else "(none)"


def _job_bucket(status: str) -> str:
    return "succeeded" if status == "succeeded" else "failed" if status in {"failed", "timed_out"} else "other"


def _distinct(unit: Unit, mode: str | None = None) -> int:
    """Pages of the unit's own (new) jobs, each page once per Veris mode."""
    new = [j for j in unit.new_jobs if mode is None or j.mode == mode]
    return sum(len({p for j in new if j.mode == m for p in j.pages}) for m in {j.mode for j in new})


# BILLABLE: only what was actually sent to Veris (one count per distinct job id).
BILLABLE = [
    ("files_sent", "Files that had pages sent to Veris"),
    ("jobs", "Veris jobs submitted (each chunk counted separately)"),
    ("pages_incl_overlap", "Pages sent to Veris - including chunk-overlap and retried pages"),
    ("pages_distinct", "Pages sent to Veris - distinct (overlap and retries counted once)"),
    ("pages_sent_twice", "  pages sent more than once (overlap + retries)"),
    ("jobs_retried", "Jobs retried or re-sent (possible double billing)"),
    ("pages_in_retried_jobs", "  pages in those retried jobs"),
    ("jobs_succeeded", "Veris jobs: succeeded"), ("jobs_failed", "Veris jobs: failed or timed out"), ("jobs_other", "Veris jobs: other / still running"),
    ("jobs_reader_duplicate", "Jobs the reader reported as already known (same key)"),
]
# LOCAL OCR ONLY: information, never added to the billable figures.
LOCAL = [
    ("files", "Uploaded files (documents read)"), ("unique_files", "Unique files (distinct content)"),
    ("files_local_only", "Files read by local OCR only (no Veris job)"),
    ("files_reused_only", "Files that reused another file's Veris jobs (no new job)"),
    ("files_not_processed", "Files with no Veris job and no local reading recorded"),
    ("pages_total", "Pages in files (where the page count is known)"),
    ("pages_not_sent", "Pages not sent to Veris (local OCR / ignored)"),
    ("pages_ignored", "  of which ignored (blank / not a resume, passport or Aadhaar page)"),
    ("pages_unresolved", "  of which could not be identified"),
    ("files_pages_unknown", "Files whose page count is unknown (left out of the page figures)"),
    ("status_completed", "Files: completed"), ("status_needs_review", "Files: needs review"), ("status_failed", "Files: failed"),
    ("status_other", "Files: other status (pending, attached, ...)"),
]


def billable_figures(units: list[Unit], mode: str | None = None) -> dict:
    out = defaultdict(int)
    for unit in units:
        jobs = [j for j in unit.new_jobs if mode is None or j.mode == mode]
        if jobs:
            out["files_sent"] += 1
        for job in jobs:
            out["jobs"] += 1
            out["jobs_" + _job_bucket(job.status)] += 1
            out["pages_incl_overlap"] += len(job.pages)
            if job.superseded or job.retry_generation > 0:
                out["jobs_retried"] += 1
                out["pages_in_retried_jobs"] += len(job.pages)
            out["jobs_reader_duplicate"] += 1 if job.duplicate else 0
            out["jobs_no_id"] += 1 if job.synthetic_id else 0
        out["pages_distinct"] += _distinct(unit, mode)
    out["pages_sent_twice"] = out["pages_incl_overlap"] - out["pages_distinct"]
    return {**{name: 0 for name, _ in BILLABLE}, **out}


def local_figures(units: list[Unit]) -> dict:
    out = defaultdict(int)
    hashes: set[str] = set()
    for unit in units:
        out["files"] += 1
        hashes.add(unit.content_hash or f"id:{unit.unit_id}")
        if unit.new_jobs:
            pass
        elif unit.jobs:
            out["files_reused_only"] += 1
        elif unit.inspected or unit.status in {"completed", "needs_review", "attached"}:
            out["files_local_only"] += 1
        else:
            out["files_not_processed"] += 1
        out["status_" + (unit.status if unit.status in {"completed", "needs_review", "failed"} else "other")] += 1
        if unit.pages_total is None:
            out["files_pages_unknown"] += 1
        else:
            out["pages_total"] += unit.pages_total
            out["pages_not_sent"] += max(unit.pages_total - len(unit.sent_pages), 0)
            out["pages_ignored"] += unit.ignored
            out["pages_unresolved"] += unit.unresolved
    out["unique_files"] = len(hashes)
    return {**{name: 0 for name, _ in LOCAL}, **out}


def figures(units: list[Unit]) -> dict:
    return {**local_figures(units), **billable_figures(units)}


def _month(unit: Unit) -> str:
    return f"{unit.created_at:%Y-%m}"


def _table(title: str, heads: list[str], items, columns, groups: list[list[Unit]], total_units: list[Unit]) -> list[list]:
    rows = [[title], ["Metric", *heads, "Total"]]
    figs = [columns(g) for g in groups]
    total = columns(total_units)
    for name, label in items:
        rows.append([label, *(f[name] for f in figs), total[name]])
    rows.append([])
    return rows


def build_sheets(data: dict) -> dict[str, list[list]]:
    units, users = data["units"], data["users"]
    ours = [u for u in units if not u.excluded]
    out = [u for u in units if u.excluded]
    sheets: dict[str, list[list]] = {}
    span = f"{data['date_from'] or 'all time'} to {data['date_to'] or 'today'}"
    cohorts = [TRAVELLED, CURRENT]
    by_cohort = [[u for u in ours if u.candidate["cohort"] == c] for c in cohorts]

    summary: list[list] = [["VERIS OCR USAGE - BILLABLE VOLUME FOR OUR CANDIDATES"], ["Period (document created date)", span],
                           ["Generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")],
                           ["Billable = pages of resume / passport / Aadhaar actually sent to Veris. Local OCR work is NOT billable and is shown separately."],
                           ["Counted: documents of candidates that exist in the CRM (Travelled + Current). Others: sheet '" + EXCLUDED_SHEET + "'."], []]
    # per-mode columns: billable_figures(ours, mode); the Total column counts a file once even if it sent several kinds
    rows = [["A. BILLABLE (sent to Veris) - by document kind"], ["Metric", *(KIND_LABEL[m] for m in MODES), "Total"]]
    per_mode = [billable_figures(ours, m) for m in MODES]
    total_b = billable_figures(ours)
    for name, label in BILLABLE:
        rows.append([label, *(f[name] for f in per_mode), total_b[name]])
    rows += [["  (Total 'files that had pages sent' counts a file once even when it sent several kinds)"], []]
    summary += rows
    summary += _table("B. BILLABLE (sent to Veris) - by billing cohort", cohorts, BILLABLE, billable_figures, by_cohort, ours)
    summary += _table("C. LOCAL OCR ONLY - NOT BILLED (information only; never added to section A or B)", cohorts, LOCAL, local_figures, by_cohort, ours)
    kinds_files = [[u for u in ours if u.kind == k] for k in KIND_ROWS]
    summary += _table("D. Local OCR only - by uploaded file type (NOT BILLED)", [KIND_LABEL[k] for k in KIND_ROWS], LOCAL, local_figures, kinds_files, ours)
    excluded = {**local_figures(out), **billable_figures(out)}
    summary.append([EXCLUDED_TITLE + " (in none of the figures above)"])
    for reason in ("not linked to a candidate", "dismissed"):
        part = {**local_figures([u for u in out if u.excluded == reason]), **billable_figures([u for u in out if u.excluded == reason])}
        summary.append([f"  {reason}", f"{part['files']} files", f"{part['files_sent']} sent to Veris", f"{part['jobs']} Veris jobs",
                        f"{part['pages_incl_overlap']} pages sent"])
    summary.append(["  total excluded", f"{excluded['files']} files", f"{excluded['files_sent']} sent to Veris", f"{excluded['jobs']} Veris jobs",
                    f"{excluded['pages_incl_overlap']} pages sent"])
    sheets["Summary"] = summary

    # ---- per candidate: file count, files sent to Veris, billable jobs / pages per kind
    heads = ["Candidate", "Care India ID", "Traveller ID", "Cohort", "Recruiter", "Files (all)", "Files sent to Veris", "Files local OCR only (not billed)"]
    for m in MODES:
        heads += [f"{KIND_LABEL[m]} files sent", f"{KIND_LABEL[m]} jobs", f"{KIND_LABEL[m]} pages (incl. overlap)", f"{KIND_LABEL[m]} pages (distinct)"]
    heads += ["Total jobs", "Total pages (incl. overlap)", "Total pages (distinct)", "Jobs retried / re-sent", "Pages not sent (local, not billed)"]
    per_candidate = [heads]
    grouped: dict[str, list[Unit]] = defaultdict(list)
    for u in ours:
        grouped[u.candidate_id].append(u)
    for cid, rows_ in sorted(grouped.items(), key=lambda kv: (kv[1][0].candidate["name"].lower(), kv[0])):
        c = rows_[0].candidate
        local, total = local_figures(rows_), billable_figures(rows_)
        line = [c["name"], c["care_id"], c["traveller_id"], c["cohort"], users.get(c["recruiter"], "(none)") if c["recruiter"] else "(none)",
                local["files"], total["files_sent"], local["files_local_only"]]
        for m in MODES:
            f = billable_figures(rows_, m)
            line += [f["files_sent"], f["jobs"], f["pages_incl_overlap"], f["pages_distinct"]]
        line += [total["jobs"], total["pages_incl_overlap"], total["pages_distinct"], total["jobs_retried"], local["pages_not_sent"]]
        per_candidate.append(line)
    per_candidate.append(["TOTAL", "", "", "", "", *[sum(r[i] for r in per_candidate[1:]) for i in range(5, len(heads))]])
    sheets["Per candidate"] = per_candidate

    # ---- per file
    detail = [["Candidate", "Care India ID", "Traveller ID", "Cohort", "Recruiter", "Source", "File name", "Uploaded as", "Pages in file",
               "Veris jobs (billable)", "Pages sent (incl. overlap)", "Distinct pages sent", "Resume pages sent", "Passport pages sent", "Aadhaar pages sent",
               "Jobs retried / re-sent", "Veris job status", "File status", "Local OCR only (not billed)", "Reused another file's jobs",
               "Same content seen in files", "Created", "Linked via", "Note"]]
    hash_count: dict[str, int] = defaultdict(int)
    for u in ours:
        hash_count[u.content_hash or f"id:{u.unit_id}"] += 1
    for u in sorted(ours, key=lambda x: ((x.candidate["name"] or "").lower(), x.created_at, x.unit_id)):
        new = u.new_jobs
        buckets = defaultdict(int)
        for job in new:
            buckets[_job_bucket(job.status)] += 1
        local_only = not u.jobs and (u.inspected or u.status in {"completed", "needs_review", "attached"})
        note = "; ".join(n for n in (u.note, "page count unknown" if u.pages_total is None else "",
                                     "result copied from another file" if u.jobs and not new else "") if n)
        detail.append([u.candidate["name"], u.candidate["care_id"], u.candidate["traveller_id"], u.candidate["cohort"], _recruiter(u, users),
                       SOURCE_LABEL[u.source], u.filename, KIND_LABEL[u.kind], u.pages_total if u.pages_total is not None else "unknown",
                       len(new), sum(len(j.pages) for j in new), _distinct(u),
                       *(sum(len(j.pages) for j in new if j.mode == m) for m in MODES),
                       sum(1 for j in new if j.superseded or j.retry_generation > 0),
                       ", ".join(f"{k} {v}" for k, v in sorted(buckets.items())) or "-", u.status, "yes" if local_only else "no",
                       "yes" if u.jobs and not new else "no", hash_count[u.content_hash or f"id:{u.unit_id}"], f"{u.created_at:%Y-%m-%d}", u.link, note])
    sheets["Per file"] = detail

    months = sorted({_month(u) for u in ours})
    monthly = [["Month", "Files (all)", "Files sent to Veris", "Veris jobs", "Pages sent (incl. overlap)", "Distinct pages sent", "Jobs retried / re-sent",
                "Resume jobs", "Passport jobs", "Aadhaar jobs", "Travelled: jobs", "Travelled: pages sent", "Current: jobs", "Current: pages sent",
                "Files local OCR only (not billed)"]]
    def month_row(label, rows_):
        b, l = billable_figures(rows_), local_figures(rows_)
        trav, cur = (billable_figures([u for u in rows_ if u.candidate["cohort"] == c]) for c in cohorts)
        return [label, l["files"], b["files_sent"], b["jobs"], b["pages_incl_overlap"], b["pages_distinct"], b["jobs_retried"],
                *(billable_figures(rows_, m)["jobs"] for m in MODES), trav["jobs"], trav["pages_incl_overlap"], cur["jobs"], cur["pages_incl_overlap"],
                l["files_local_only"]]
    for month in months:
        monthly.append(month_row(month, [u for u in ours if _month(u) == month]))
    monthly.append(month_row("Total", ours))
    sheets["Per month"] = monthly

    recruiters = sorted({_recruiter(u, users) for u in ours})
    by_recruiter = [["Recruiter", "Files (all)", "Files sent to Veris", "Veris jobs", "Pages sent (incl. overlap)", "Distinct pages sent",
                     "Jobs retried / re-sent", "Files local OCR only (not billed)"]]
    for name in recruiters:
        rows_ = [u for u in ours if _recruiter(u, users) == name]
        b, l = billable_figures(rows_), local_figures(rows_)
        by_recruiter.append([name, l["files"], b["files_sent"], b["jobs"], b["pages_incl_overlap"], b["pages_distinct"], b["jobs_retried"], l["files_local_only"]])
    sheets["Per recruiter"] = by_recruiter

    excluded_rows = [[EXCLUDED_TITLE + " - NOT in any total. No personal data on this sheet."],
                     ["Reason", "Source", "Uploaded as", "Pages in file", "Pages sent (incl. overlap)", "Distinct pages sent", "Veris jobs", "Jobs retried / re-sent",
                      "File status", "Recruiter", "Created", "Reference"]]
    for u in sorted(out, key=lambda x: (x.excluded, x.created_at, x.unit_id)):
        new = u.new_jobs
        excluded_rows.append([u.excluded, SOURCE_LABEL[u.source], KIND_LABEL[u.kind], u.pages_total if u.pages_total is not None else "unknown",
                              sum(len(j.pages) for j in new), _distinct(u), len(new), sum(1 for j in new if j.superseded or j.retry_generation > 0),
                              u.status, _recruiter(u, users), f"{u.created_at:%Y-%m-%d}", u.unit_id[:8]])
    excluded_rows.append(["TOTAL", "", "", "", excluded["pages_incl_overlap"], excluded["pages_distinct"], excluded["jobs"], excluded["jobs_retried"]])
    sheets[EXCLUDED_SHEET] = excluded_rows

    counts = data["counts"]
    sheets["Notes"] = [[line] for line in [
        "NOTES - what is counted and what is not",
        "",
        "BILLABLE vs NOT BILLABLE: the local OCR reads and classifies every file first and cuts it. Only the pages of resume / passport / Aadhaar that were then sent to Veris are billable (sections A and B, the 'Per candidate' sheet, and the billable columns of the other sheets).",
        "Everything handled only by local OCR (files read locally, pages the local check ignored or did not send) is shown in section C/D and in the 'local OCR only (not billed)' columns. It is information and is never added to a billable figure.",
        "WHAT IS A VERIS JOB: one `jobs.submit` call to Veris, identified by a job id that the CRM saved. A long PDF (over 20 pages) is cut into chunks and every chunk is its own job; a passport of several pages is one job per page.",
        "Jobs are counted once per distinct Veris job id, even when the same checkpoint was copied to another file (a re-uploaded or reused file shows 'Reused another file's jobs' and adds no jobs).",
        "Superseded attempts (earlier failed or replaced jobs kept in provider_job_history) are counted as jobs, and also shown as 'retried / re-sent', because Veris may have charged for them.",
        "PAGES: 'incl. overlap' adds the pages of every job. A resume chunk repeats one page of the previous chunk (overlap), so those pages are sent twice; 'distinct pages' counts each page once per file and mode. A page sent in two modes (for example resume and passport) is counted once per mode, as Veris sees it.",
        "FILES: 'files that had pages sent' per kind = files with at least one new Veris job in that mode (resume+ is merged into resume). A combined file can send pages in several modes, so it counts in each of those kinds, but once in the Total. Section D splits the non-billable local-only figures by the type chosen at upload.",
        "LOCAL ONLY: a file with no Veris job id and with a local inspection (or status completed / needs_review / attached). A file with no job and no recorded inspection is shown separately ('no local reading recorded'): it may not have been processed yet, or its state is empty.",
        "",
        "OUR CANDIDATES: the totals, per-candidate, per-cohort, per-month, per-recruiter and per-file figures include only documents that resolve to a candidate present in the table crm_candidates (candidates deleted in the CRM are gone from that table and their documents were deleted with them).",
        "A document is linked to a candidate through: its candidate_id; else its attachment (document_attachments, entity candidate); else another document of the same upload group that has a candidate; else a committed person group of a multi-person review. Email, WhatsApp and person-group documents use the candidate of their intake record.",
        "A file shared by several candidates in a multi-person review is counted once, under the first candidate (marked in the note).",
        f"NOT COUNTED (listed on '{EXCLUDED_SHEET}', without personal data): documents not linked to any candidate (including abandoned drafts and lead resume checks never attached), and every document with status dismissed. Excluded Veris jobs are still real spend with Veris; they are only kept out of the client's total.",
        "When the same Veris job id appears in a document of ours and in an excluded document, it is counted for ours.",
        "",
        f"COHORTS: '{TRAVELLED}' = candidate status deployed, or a deployed application, or a Traveller ID. Everything else is '{CURRENT}'. A candidate is in one cohort for all of their documents, as of the time the report runs (not as of the upload date).",
        "MONTHS use the created date of the document row (email: of the ingestion job; WhatsApp: received date; person group: review created date), not the time Veris was called. The --from/--to filter uses the same date (UTC).",
        "RECRUITER: the owner of the upload; for email, WhatsApp and person-group documents the candidate's intake recruiter (or current owner). On the 'Per candidate' sheet: the candidate's intake recruiter (or current owner).",
        "",
        "LIMITS - why numbers may differ from the Veris invoice:",
        "1. Veris may bill per job or per page (or per page with a minimum per job). Both figures are shown; the invoice unit decides which applies. Retried jobs that failed may or may not be billed.",
        "2. Only jobs whose job id the CRM saved are known. A request lost before the answer was saved cannot be seen; a job whose id is missing but whose result is saved is counted once (see 'jobs without a saved id' below).",
        "3. Rows created before state tracking, or with an empty state, have no job figures (counted as files only). The number of such rows is given below.",
        "4. Page totals come from the saved inspection or coverage; where they are absent the file is listed as 'page count unknown' and is left out of the pages-not-sent figures (the billable figures do not need them).",
        "5. The direct scan endpoint (/api/ocr/{kind}) does not keep a checkpoint, so anything sent through it cannot be reported. Documents deleted together with a deleted candidate are gone from the CRM and cannot be reported.",
        "6. 'Pages sent' are the pages selected for each job. The CRM may reduce a page's size before sending, but a page is a page for billing.",
        "7. Veris job statuses are the last status the CRM saw (a job still 'running' when last checked may have finished later).",
        "",
        "RUN FIGURES: documents read: " + ", ".join(f"{SOURCE_LABEL[k]} {v}" for k, v in sorted(counts['rows_read'].items())) +
        f"; upload rows with an empty state: {counts['empty_state']}; jobs without a saved id: {counts['no_job_id']}; "
        f"files with unknown page count (counted in ours): {local_figures(ours)['files_pages_unknown']}; documents excluded: {len(out)}.",
    ]]
    return sheets


# ----------------------------------------------------------------------------- output

def write_report(sheets: dict[str, list[list]], out_dir: Path, fmt: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font
        book = Workbook()
        book.remove(book.active)
        for name, rows in sheets.items():
            sheet = book.create_sheet(name[:31])
            for row in rows:
                sheet.append(row)
            for cell in sheet[1]:
                cell.font = Font(bold=True)
            if name not in {"Summary", "Notes"}:
                header = 2 if name == EXCLUDED_SHEET else 1
                for cell in sheet[header]:
                    cell.font = Font(bold=True)
                sheet.freeze_panes = sheet.cell(row=header + 1, column=1)
            for index in range(1, (sheet.max_column or 1) + 1):
                letter = sheet.cell(row=1, column=index).column_letter
                longest = max((len(str(c.value)) for c in sheet[letter][:200] if c.value is not None), default=8)
                sheet.column_dimensions[letter].width = 120 if name == "Notes" else min(max(longest + 2, 10), 70 if name == "Summary" and index == 1 else 38)
        path = out_dir / f"veris-usage-{stamp}.xlsx"
        book.save(path)
        return [path]
    paths = []
    for name, rows in sheets.items():
        path = out_dir / f"veris-usage-{stamp}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            csv.writer(handle).writerows(rows)
        paths.append(path)
    return paths


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("use YYYY-MM-DD") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Veris OCR usage report (our candidates only).")
    parser.add_argument("--database-url", help="SQLAlchemy URL; default: the app settings (backend/.env)")
    parser.add_argument("--from", dest="date_from", type=_date, help="first document created date, YYYY-MM-DD (default: all time)")
    parser.add_argument("--to", dest="date_to", type=_date, help="last document created date, YYYY-MM-DD, inclusive (default: all time)")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--format", choices=("xlsx", "csv"), default=None, help="default: xlsx when openpyxl is installed, else csv")
    args = parser.parse_args(argv)
    fmt = args.format
    if fmt in (None, "xlsx"):
        try:
            import openpyxl  # noqa: F401
            fmt = fmt or "xlsx"
        except ImportError:
            if fmt == "xlsx":
                print("openpyxl is not installed in this Python; install it or use --format csv.", file=sys.stderr)
                return 2
            fmt = "csv"
            print("openpyxl is not installed: writing CSV files instead.")
    url = args.database_url
    if not url:
        from app.core.config import get_settings
        url = get_settings().database_url
    print("Database (read only):", describe(url))
    engine = read_only_engine(url)
    try:
        with engine.connect() as conn:
            begin_read_only(conn)
            data = collect(conn, args.date_from, args.date_to)
            conn.rollback()
    finally:
        engine.dispose()
    sheets = build_sheets(data)
    paths = write_report(sheets, Path(args.out_dir), fmt)
    print()
    for row in sheets["Summary"]:
        print(" | ".join(str(c) for c in row))
    print()
    for path in paths:
        print("Wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
