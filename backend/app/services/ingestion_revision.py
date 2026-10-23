"""Small, non-sensitive change token for committed intake and candidate updates."""

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import Candidate, EmailIngestionJob, WhatsAppMessage


def _signature(count_column, stamp_column, *conditions):
    """`count:epoch` for one table as a scalar subquery.

    Epoch formatting is independent of the connection's timezone. The count makes
    deletions visible even when the newest remaining timestamp is unchanged.
    """
    return (
        select(
            func.concat(
                cast(func.count(count_column), String),
                ":",
                func.coalesce(cast(func.extract("epoch", func.max(stamp_column)), String), "0"),
            )
        )
        .where(*conditions)
        .scalar_subquery()
    )


def revision_expression():
    # Read only aggregate metadata: never document state, mailbox contents,
    # extraction payloads or provider credentials. Every intake channel
    # (email, WhatsApp) finishes by creating or enriching a candidate, and bundle
    # commits and merges also touch candidates, so candidate changes are part of
    # the token alongside completed email and WhatsApp receipts.
    return func.concat_ws(
        ":",
        _signature(EmailIngestionJob.id, EmailIngestionJob.updated_at, EmailIngestionJob.status == "completed"),
        _signature(WhatsAppMessage.id, WhatsAppMessage.updated_at, WhatsAppMessage.status == "completed"),
        _signature(Candidate.id, Candidate.updated_at),
    )


def read_revision(db: Session) -> str:
    return db.scalar(select(revision_expression()))
