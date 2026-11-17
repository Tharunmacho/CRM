"""What happened to each email: the Inbox "Ingested" view (mounted under /api/inbox by inbox.py).

Who may call it: any signed-in user, exactly like GET /api/inbox (the existing inbox endpoints do not scope by mailbox
or role; the Data Bank is open to every staff member). Not signed in: 401.

CONTRACT (the frontend builds against exactly this; keys are camelCase, times are ISO 8601)

GET /api/inbox/ingested?account=<mailbox id | all>&status=<all|ingested|review|failed|ignored|pending>&q=<text>&page=<0-based>&page_size=<10..50, default 25>
  -> {"items": [{
        "id": <job id>, "messageId": <MailMessage id>, "accountId": ..., "accountAddress": "jobs@...",
        "receivedAt": ISO, "processedAt": ISO | null,            # null while still waiting
        "from": {"name": str | null, "address": str}, "subject": str,
        "attachments": [{"name": str, "sizeKb": int}],           # names only, at most 8, by file name
        "attachmentCount": int,                                   # all of them
        "status": "ingested" | "review" | "failed" | "ignored" | "pending",
        "candidate": {"id", "ref", "careId", "name"} | null,
        "created": true | false | null,                           # true: this email created the candidate; false: it added documents to an existing one; null: no candidate
        "note": str | null,                                       # short plain-English reason, at most 200 characters, identity numbers hidden
        "documentsFiled": int | null                              # attachments of this email filed on the candidate (ingested only)
     }], "total": int, "page": int, "pageSize": int}
  Newest first (receivedAt desc, id desc). `q` matches the subject, sender address or name and the candidate's name
  (case-insensitive, literal text: % and _ are not wildcards). An unknown mailbox id gives an empty list.
  Status buckets (from the job's status): ingested = completed with a candidate; review = needs_review (or completed without
  a candidate); failed = failed; ignored = ignored (no resume, duplicate file, nothing to read); pending = pending, processing, retry.

GET /api/inbox/ingested/summary
  -> {"ingested": int, "createdCandidates": int, "review": int, "failed": int, "ignored": int, "pending": int,
      "intake": {"ingestedCandidates": int, "review": int, "accounts": int},
      "accounts": [{"id", "address", "provider", "connectionStatus": "connected" | "error" | "disconnected",
                    "autoImport": bool, "lastSyncAt": ISO | null, "lastError": str | null,
                    "ingested": int, "review": int, "failed": int, "ignored": int, "pending": int}]}
  `ingested` counts completed jobs that ended with a candidate (created or enriched); `createdCandidates` counts the
  distinct candidates created by email (source "email"). Per-mailbox counts add up to the totals.

Dashboard "Inbox" card: GET /api/inbox (the call the card already makes, `?limit=1`) gains a top-level
  "intake": {"ingestedCandidates": int, "review": int, "accounts": int}   (candidates created by email, jobs to review, connected mailboxes)
  and keeps every existing key.

Statement budget (one of them is always the sign-in check): the list is 2 statements (3 for a page past the end), the summary 4.
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import current_user
from app.db.models import User
from app.db.session import get_db
from app.services.inbox_ingested import ingested_page, ingested_summary

router = APIRouter(tags=["inbox"])


@router.get("/ingested")
def ingested(db: Session = Depends(get_db), user: User = Depends(current_user),
             account: Annotated[str, Query(max_length=64)] = "all",
             status: Literal["all", "ingested", "review", "failed", "ignored", "pending"] = "all",
             q: Annotated[str, Query(max_length=200)] = "",
             page: Annotated[int, Query(ge=0, le=100_000)] = 0,
             page_size: Annotated[int, Query(ge=10, le=50)] = 25):
    return ingested_page(db, account=account, status=status, q=q, page=page, page_size=page_size)


@router.get("/ingested/summary")
def ingested_counts(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return ingested_summary(db)
