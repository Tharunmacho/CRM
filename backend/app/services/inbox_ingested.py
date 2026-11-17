"""Read-side summaries of what happened to each email: "candidate created", needs review, failed, ignored, waiting.

Everything here is read-only and shaped for a remote database (every statement is a network round trip):
the list is ONE statement (joins for message, mailbox and candidate; the attachment names, count and filed
documents ride along as correlated subqueries that Postgres evaluates only for the page's rows) and the
summary is ONE grouped statement over jobs plus the mailbox read. Nothing here loads message bodies,
OCR state (`EmailIngestionJob.state` is deferred and never selected) or identity numbers.
"""
import re
from typing import Literal

from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.orm import Session, load_only

from app.db.models import (
    Attachment, Candidate, EmailIngestionJob, MailAccount, MailAttachment, MailMessage, Source,
)
from app.services.candidate_search import literal_contains
from app.services.parallel_reads import parallel_reads

Outcome = Literal["ingested", "review", "failed", "ignored", "pending"]
OUTCOMES: tuple[str, ...] = ("ingested", "review", "failed", "ignored", "pending")
PENDING_STATES = ("pending", "processing", "retry")
NOTE_LIMIT = 200
MAX_ATTACHMENTS = 8

# Identity numbers never leave this module: a run of six or more digits (phone, Aadhaar, passport digits, even when
# written in groups) and any word of seven or more letters-and-digits that mixes both (passport style numbers).
_DIGIT_RUN = re.compile(r"\d(?:[ \-]?\d){5,}")
_MIXED_CODE = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{7,}\b")


def outcome_of(status: str, has_candidate: bool) -> str:
    """The five buckets people care about, from a job's status and whether it ended with a candidate."""
    if status == "completed":
        return "ingested" if has_candidate else "review"
    if status == "needs_review":
        return "review"
    if status == "failed":
        return "failed"
    if status == "ignored":
        return "ignored"
    return "pending"


def outcome_filter(outcome: str):
    """The same buckets as a WHERE clause (plain column tests, so Postgres can use the table's own indexes)."""
    completed = EmailIngestionJob.status == "completed"
    if outcome == "ingested":
        return and_(completed, EmailIngestionJob.candidate_id.isnot(None))
    if outcome == "review":
        return or_(EmailIngestionJob.status == "needs_review", and_(completed, EmailIngestionJob.candidate_id.is_(None)))
    if outcome == "failed":
        return EmailIngestionJob.status == "failed"
    if outcome == "ignored":
        return EmailIngestionJob.status == "ignored"
    if outcome == "pending":
        return EmailIngestionJob.status.in_(PENDING_STATES)
    raise ValueError(outcome)


def clean_text(value: str | None, limit: int = NOTE_LIMIT) -> str | None:
    """Plain one-line text: whitespace collapsed, identity-looking numbers hidden, trimmed to `limit`."""
    text = " ".join((value or "").split())
    if not text:
        return None
    text = _MIXED_CODE.sub("[hidden]", _DIGIT_RUN.sub("[hidden]", text))
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def note_for(outcome: str, last_error: str | None, *, candidate_needs_review: bool = False, attachment_count: int = 0) -> str | None:
    """A short plain-English reason for what happened, or None when there is nothing to add."""
    text = clean_text(last_error)
    if outcome == "ingested":
        if text:
            return text
        return "Candidate saved, but flagged for review: check the name and phone" if candidate_needs_review else None
    if outcome == "ignored":
        if not text:
            return "No resume found in the attachments" if attachment_count else "This email has no attachments to read"
        if text.startswith("No resume, passport or Aadhaar"):
            return "No resume found in the attachments"
        return text
    if outcome == "review":
        if not text:
            return "Needs review: the documents could not be matched to one person"
        if text.startswith("One or more document pages need review"):
            return "Needs review: some pages could not be read cleanly, or the documents name different people"
        return text
    if outcome == "failed":
        return text or "Processing failed"
    return text or "Waiting to be read"


def _page_query(filters: list):
    names = (
        select(func.jsonb_agg(aggregate_order_by(
            func.jsonb_build_object("name", MailAttachment.file_name, "sizeKb", func.coalesce(MailAttachment.size_kb, 0)),
            MailAttachment.file_name, MailAttachment.id)))
        .where(MailAttachment.message_id == MailMessage.id).correlate(MailMessage).scalar_subquery()
    )
    attachment_count = (
        select(func.count()).where(MailAttachment.message_id == MailMessage.id).correlate(MailMessage).scalar_subquery()
    )
    # Attachments of this email that were filed on the candidate (the original file is linked under the "source_bundle" slot).
    filed = (
        select(func.count(distinct(Attachment.id)))
        .select_from(MailAttachment)
        .join(Attachment, and_(
            Attachment.entity_type == "candidate", Attachment.entity_id == EmailIngestionJob.candidate_id,
            Attachment.slot == "source_bundle", Attachment.content_hash == MailAttachment.content_hash))
        .where(MailAttachment.message_id == MailMessage.id)
        .correlate(MailMessage, EmailIngestionJob).scalar_subquery()
    )
    created = or_(
        Candidate.source_detail == func.concat("email_message:", MailMessage.id),
        Candidate.ref == func.concat("CND-E-", func.replace(EmailIngestionJob.id, "-", "")),
    )
    return (
        select(
            EmailIngestionJob.id, EmailIngestionJob.message_id, EmailIngestionJob.status, EmailIngestionJob.last_error,
            EmailIngestionJob.candidate_id, EmailIngestionJob.updated_at,
            MailMessage.account_id, MailMessage.received_at, MailMessage.from_name, MailMessage.from_address, MailMessage.subject,
            MailAccount.address.label("account_address"),
            Candidate.ref, Candidate.care_id, Candidate.name.label("candidate_name"), Candidate.needs_review.label("candidate_review"),
            created.label("created"), names.label("attachment_names"), attachment_count.label("attachment_count"),
            filed.label("filed"), func.count().over().label("total"),
        )
        .select_from(EmailIngestionJob)
        .join(MailMessage, MailMessage.id == EmailIngestionJob.message_id)
        .join(MailAccount, MailAccount.id == MailMessage.account_id)
        .outerjoin(Candidate, Candidate.id == EmailIngestionJob.candidate_id)
        .where(*filters)
    )


def _filters(account: str, status: str, q: str) -> list:
    filters = []
    if account and account != "all":
        filters.append(MailMessage.account_id == account)
    if status != "all":
        filters.append(outcome_filter(status))
    text = " ".join((q or "").split())
    if text:
        filters.append(or_(*[literal_contains(column, text) for column in (
            MailMessage.subject, MailMessage.from_address, MailMessage.from_name, Candidate.name)]))
    return filters


def ingested_page(db: Session, *, account: str = "all", status: str = "all", q: str = "", page: int = 0, page_size: int = 25) -> dict:
    """One page of "what happened to each email", newest first. One statement (a second only for a page past the end)."""
    filters = _filters(account, status, q)
    rows = db.execute(
        _page_query(filters)
        .order_by(MailMessage.received_at.desc(), EmailIngestionJob.id.desc())
        .offset(page * page_size).limit(page_size)
    ).all()
    if rows:
        total = rows[0].total
    elif page == 0:
        total = 0
    else:  # a page past the end: the window count has no row to ride on
        total = db.scalar(
            select(func.count()).select_from(EmailIngestionJob)
            .join(MailMessage, MailMessage.id == EmailIngestionJob.message_id)
            .outerjoin(Candidate, Candidate.id == EmailIngestionJob.candidate_id).where(*filters)) or 0
    items = []
    for row in rows:
        has_candidate = row.candidate_id is not None
        outcome = outcome_of(row.status, has_candidate)
        names = row.attachment_names or []
        items.append({
            "id": row.id, "messageId": row.message_id, "accountId": row.account_id, "accountAddress": row.account_address,
            "receivedAt": row.received_at, "processedAt": row.updated_at if outcome != "pending" else None,
            "from": {"name": row.from_name or None, "address": row.from_address or ""},
            "subject": row.subject or "(No subject)",
            "attachments": [{"name": item["name"], "sizeKb": item["sizeKb"]} for item in names[:MAX_ATTACHMENTS]],
            "attachmentCount": row.attachment_count or 0,
            "status": outcome,
            "candidate": {"id": row.candidate_id, "ref": row.ref, "careId": row.care_id, "name": row.candidate_name} if has_candidate else None,
            "created": bool(row.created) if outcome == "ingested" else None,
            "note": note_for(outcome, row.last_error, candidate_needs_review=bool(row.candidate_review), attachment_count=row.attachment_count or 0),
            "documentsFiled": (row.filed or 0) if outcome == "ingested" else None,
        })
    return {"items": items, "total": total, "page": page, "pageSize": page_size}


def _zero() -> dict:
    return {name: 0 for name in OUTCOMES}


def connection_status(account: MailAccount) -> str:
    return "disconnected" if account.disconnected_at else "error" if account.last_error else "connected"


def ingested_summary(db: Session) -> dict:
    """Counts by outcome overall and per mailbox: one grouped statement over jobs, plus the mailbox list and a candidate count."""
    def read_groups(session):
        return session.execute(
            select(MailMessage.account_id, EmailIngestionJob.status, EmailIngestionJob.candidate_id.isnot(None), func.count())
            .select_from(EmailIngestionJob).join(MailMessage, MailMessage.id == EmailIngestionJob.message_id)
            .group_by(MailMessage.account_id, EmailIngestionJob.status, EmailIngestionJob.candidate_id.isnot(None))
        ).all()

    def read_accounts(session):
        return list(session.scalars(select(MailAccount).options(load_only(
            MailAccount.id, MailAccount.address, MailAccount.provider, MailAccount.auto_ingest, MailAccount.last_sync_at,
            MailAccount.last_error, MailAccount.disconnected_at)).order_by(MailAccount.address)))

    def read_created(session):
        return session.scalar(select(func.count()).select_from(Candidate).where(Candidate.source == Source.email)) or 0

    groups, accounts, created = parallel_reads(db, read_groups, read_accounts, read_created)
    per_account: dict[str, dict] = {}
    total = _zero()
    for account_id, status, has_candidate, count in groups:
        outcome = outcome_of(status, bool(has_candidate))
        per_account.setdefault(account_id, _zero())[outcome] += count
        total[outcome] += count
    connected = sum(1 for account in accounts if not account.disconnected_at)
    return {
        **total, "createdCandidates": created,
        "intake": {"ingestedCandidates": created, "review": total["review"], "accounts": connected},
        "accounts": [{
            "id": account.id, "address": account.address, "provider": account.provider,
            "connectionStatus": connection_status(account), "autoImport": account.auto_ingest,
            "lastSyncAt": account.last_sync_at, "lastError": clean_text(account.last_error),
            **per_account.get(account.id, _zero()),
        } for account in accounts],
    }


def intake_counts_columns():
    """Two uncorrelated scalar subqueries (each evaluated once) for statements that already run: candidates created by
    email and jobs needing review. The dashboard's Inbox card rides on the inbox read this way, at no extra round trip."""
    created = select(func.count()).select_from(Candidate).where(Candidate.source == Source.email).scalar_subquery()
    review = select(func.count()).select_from(EmailIngestionJob).where(outcome_filter("review")).scalar_subquery()
    return created.label("ingested_candidates"), review.label("review_jobs")
