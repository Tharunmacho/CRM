"""Small, non-sensitive change token for committed email candidate updates."""

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from app.db.models import EmailIngestionJob


def revision_expression():
    # Candidate changes and job completion commit together. Read only aggregate
    # metadata: never document state, mailbox contents or provider credentials.
    # Epoch formatting is independent of the connection's timezone.
    return (
        select(
            func.concat(
                cast(func.count(EmailIngestionJob.id), String),
                ":",
                func.coalesce(
                    cast(func.extract("epoch", func.max(EmailIngestionJob.updated_at)), String),
                    "0",
                ),
            )
        )
        .where(EmailIngestionJob.status == "completed")
        .scalar_subquery()
    )


def read_revision(db: Session) -> str:
    return db.scalar(select(revision_expression()))
