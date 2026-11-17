"""Inbox "what happened to each email": the ingested list, its summary and the dashboard Inbox card numbers.

Only the guarded disposable PostgreSQL fixture is used; no mailbox, OCR or WhatsApp provider is ever contacted.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import (
    Attachment, Candidate, EmailIngestionJob, MailAccount, MailAttachment, MailMessage, Source,
)
from app.services import inbox_ingested
from test_bootstrap_performance import assert_no_private_data, recorded_statements
from test_email_intake import intake, receipt  # noqa: F401
from test_inbox_attention import desk  # noqa: F401
from test_database_naming import schema_database  # noqa: F401

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)
ACCOUNTS = ("account-a", "account-b", "account-c")
# One block of ten emails, repeated: three candidates (two created, one added to an existing person), two for review
# (one needs_review job, one completed job with no candidate), one failed, one ignored and three still waiting.
KINDS = ["created", "created", "existing", "review", "review_empty", "failed", "ignored", "pending", "processing", "retry"]
OUTCOME = {"created": "ingested", "existing": "ingested", "review": "review", "review_empty": "review", "failed": "failed",
           "ignored": "ignored", "pending": "pending", "processing": "pending", "retry": "pending"}
ERRORS = {
    "review": "One or more document pages need review. No candidate was changed.",
    "failed": "We could not read the documents in this email. Try again, and contact your administrator if it keeps happening.",
    "ignored": "No resume, passport or Aadhaar pages identified.",
}
SECRET_ERROR = "Passport N1234567 and phone 9876543210 or 1234 5678 9012 do not agree."


@pytest.fixture
def mail(intake, desk):  # noqa: F811
    """40 emails over three mailboxes with every kind of outcome; returns the ground truth, newest email first."""
    with intake.sessions() as db:
        db.add(MailAccount(id="account-c", address="inbox-c@example.test", provider="gmail", auto_ingest=False, sync_state={}))
        db.add(Candidate(id="existing-candidate", ref="CND-1001", care_id="CI-900", name="Existing Person", phone="+919100000000", source=Source.walk_in))
        db.commit()
    truth = []
    with intake.sessions() as db:
        for i in range(40):
            kind, account = KINDS[i % 10], ACCOUNTS[i % 3]
            message = MailMessage(
                account_id=account, provider_message_id=f"ingested:{i}", received_at=NOW - timedelta(minutes=i),
                subject=f"Application {i} for welder" if i % 2 else f"CV number {i}",
                from_name=f"Sender {i}" if i % 4 else None, from_address=f"sender{i}@example.test")
            db.add(message)
            db.flush()
            job_id = str(uuid4())
            candidate_id = None
            if kind == "created":
                candidate = Candidate(ref=f"CND-E-{job_id.replace('-', '')}", care_id=f"CI-{i + 1:03d}", name=f"Created Person {i}",
                                      phone=f"+9190000{i:05d}", source=Source.email, source_detail=f"email_message:{message.id}")
                db.add(candidate)
                db.flush()
                candidate_id = candidate.id
            elif kind == "existing":
                candidate_id = "existing-candidate"
            error = ERRORS.get(kind)
            if i == 4:
                error = SECRET_ERROR  # a review_empty job never has an error; this one is the redaction case below
            status = {"created": "completed", "existing": "completed", "review": "needs_review", "review_empty": "completed"}.get(kind, kind)
            db.add(EmailIngestionJob(
                id=job_id, message_id=message.id, status=status, last_error=error, candidate_id=candidate_id, state={},
                updated_at=NOW - timedelta(minutes=i) + timedelta(seconds=30)))
            count = i % 4
            for k in range(count):
                data = f"file {i} {k}".encode()
                digest = hashlib.sha256(data).hexdigest()
                db.add(MailAttachment(message_id=message.id, file_name=f"cv-{i}-{k}.pdf", size_kb=10 + k, content_hash=digest, object_key=f"k/{i}/{k}"))
                if candidate_id and kind == "created":
                    db.add(Attachment(entity_type="candidate", entity_id=candidate_id, slot="source_bundle", version=k + 1, file_name=f"cv-{i}-{k}.pdf",
                                      object_key=f"k/{i}/{k}", content_hash=digest))
            truth.append({"i": i, "kind": kind, "account": account, "status": OUTCOME[kind], "job": job_id, "message": message.id,
                          "attachments": count, "candidate": candidate_id})
        db.commit()
    return truth


def get(desk, **params):
    response = desk.get("/api/inbox/ingested", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body):
    return [item["id"] for item in body["items"]]


def expected(truth, **where):
    rows = [row for row in truth if all(row[key] == value for key, value in where.items())]
    return [row["job"] for row in rows]


def test_list_shows_every_outcome_newest_first_with_the_documented_shape(intake, mail, desk):
    body = get(desk, page_size=50)
    assert body["total"] == 40 and body["page"] == 0 and body["pageSize"] == 50
    assert ids(body) == [row["job"] for row in mail]  # newest first
    received = [item["receivedAt"] for item in body["items"]]
    assert received == sorted(received, reverse=True)
    by_job = {row["job"]: row for row in mail}
    for item in body["items"]:
        row = by_job[item["id"]]
        assert set(item) == {"id", "messageId", "accountId", "accountAddress", "receivedAt", "processedAt", "from", "subject",
                             "attachments", "attachmentCount", "status", "candidate", "created", "note", "documentsFiled"}
        assert item["status"] == row["status"] and item["messageId"] == row["message"] and item["accountId"] == row["account"]
        assert item["accountAddress"].startswith("inbox-") and item["attachmentCount"] == row["attachments"]
        assert [a["name"] for a in item["attachments"]] == [f"cv-{row['i']}-{k}.pdf" for k in range(row["attachments"])]
        assert item["from"] == {"name": f"Sender {row['i']}" if row["i"] % 4 else None, "address": f"sender{row['i']}@example.test"}
        assert (item["processedAt"] is None) == (row["status"] == "pending")

    created = next(item for item in body["items"] if by_job[item["id"]]["kind"] == "created" and item["attachmentCount"] == 2)
    i = by_job[created["id"]]["i"]
    assert created["candidate"] == {"id": by_job[created["id"]]["candidate"], "ref": f"CND-E-{created['id'].replace('-', '')}",
                                    "careId": f"CI-{i + 1:03d}", "name": f"Created Person {i}"}
    assert created["created"] is True and created["documentsFiled"] == 2 and created["note"] is None

    existing = next(item for item in body["items"] if by_job[item["id"]]["kind"] == "existing")
    assert existing["candidate"]["name"] == "Existing Person" and existing["candidate"]["careId"] == "CI-900"
    assert existing["created"] is False and existing["documentsFiled"] == 0

    for item in body["items"]:
        kind = by_job[item["id"]]["kind"]
        if kind in ("review", "review_empty", "failed", "ignored", "pending", "processing", "retry"):
            assert item["candidate"] is None and item["created"] is None and item["documentsFiled"] is None
            assert item["note"] and len(item["note"]) <= 200
    notes = {by_job[item["id"]]["kind"]: item["note"] for item in body["items"] if by_job[item["id"]]["i"] not in (4,)}
    assert notes["ignored"] == "No resume found in the attachments" or notes["ignored"] == "This email has no attachments to read"
    assert notes["review"].startswith("Needs review:")
    assert notes["pending"] == "Waiting to be read"
    assert notes["failed"].startswith("We could not read the documents")


def test_filters_by_account_status_and_search_with_true_totals(intake, mail, desk):
    for account in ACCOUNTS:
        body = get(desk, account=account, page_size=50)
        assert ids(body) == expected(mail, account=account) and body["total"] == len(expected(mail, account=account))
    assert get(desk, account="all", page_size=50)["total"] == 40
    assert get(desk, account="no-such-mailbox") == {"items": [], "total": 0, "page": 0, "pageSize": 25}

    for status in ("ingested", "review", "failed", "ignored", "pending"):
        body = get(desk, status=status, page_size=50)
        assert ids(body) == expected(mail, status=status), status
        assert body["total"] == len(expected(mail, status=status)) > 0
    assert get(desk, status="all", page_size=50)["total"] == 40
    both = get(desk, account="account-b", status="ingested", page_size=50)
    assert ids(both) == expected(mail, account="account-b", status="ingested")
    assert desk.get("/api/inbox/ingested", params={"status": "bogus"}).status_code == 422

    by_subject = get(desk, q="APPLICATION 1", page_size=50)  # case-insensitive
    assert by_subject["total"] == len([row for row in mail if row["i"] % 2 and f"application {row['i']}".startswith("application 1")])
    assert all("application 1" in item["subject"].lower() for item in by_subject["items"])
    assert ids(get(desk, q="sender7@example")) == [row["job"] for row in mail if row["i"] == 7]
    assert ids(get(desk, q="SENDER 5")) == [row["job"] for row in mail if row["i"] == 5]  # sender name
    by_candidate = get(desk, q="created person 3", page_size=50)
    assert [item["candidate"]["name"] for item in by_candidate["items"]] == ["Created Person 30"] or all(
        "created person 3" in item["candidate"]["name"].lower() for item in by_candidate["items"])
    assert by_candidate["total"] >= 1
    assert get(desk, q="existing person", status="ingested", page_size=50)["total"] == len(
        [row for row in mail if row["kind"] == "existing"])
    # LIKE wildcards are plain text, not patterns.
    assert get(desk, q="%")["total"] == 0 and get(desk, q="_")["total"] == 0 and get(desk, q="a%b")["total"] == 0
    assert get(desk, q="nothing matches this")["total"] == 0
    assert get(desk, q="sender", account="account-a", status="failed", page_size=50)["total"] == len(
        expected(mail, account="account-a", status="failed"))


def test_paging_walks_the_whole_list_once_in_order(intake, mail, desk):
    seen, totals = [], set()
    for page in range(4):
        body = get(desk, page=page, page_size=10)
        assert body["page"] == page and body["pageSize"] == 10 and len(body["items"]) == 10
        seen += ids(body)
        totals.add(body["total"])
    assert seen == [row["job"] for row in mail] and totals == {40}
    past = get(desk, page=4, page_size=10)
    assert past == {"items": [], "total": 40, "page": 4, "pageSize": 10}
    assert get(desk, page=100, page_size=10, status="failed")["total"] == len(expected(mail, status="failed"))
    uneven = get(desk, page=1, page_size=25)
    assert len(uneven["items"]) == 15 and uneven["total"] == 40 and ids(uneven) == [row["job"] for row in mail][25:]
    assert get(desk)["pageSize"] == 25 and len(get(desk)["items"]) == 25
    for bad in ({"page_size": 9}, {"page_size": 51}, {"page": -1}):
        assert desk.get("/api/inbox/ingested", params=bad).status_code == 422


def test_ties_on_receive_time_keep_a_stable_order_by_id(intake, mail, desk):
    with intake.sessions() as db:
        for message in db.query(MailMessage).filter(MailMessage.provider_message_id.like("ingested:%")):
            message.received_at = NOW
        db.commit()
    first = ids(get(desk, page_size=50))
    assert first == sorted(first, reverse=True)
    assert [ids(get(desk, page=p, page_size=10)) for p in range(4)] == [first[0:10], first[10:20], first[20:30], first[30:40]]


def test_everyone_signed_in_reads_the_same_list_and_the_rest_get_401(intake, mail, desk):
    admin_ids = ids(get(desk, page_size=50))
    for user in ("attention-manager", "attention-staff"):  # the inbox is open to every role, like GET /api/inbox
        desk.as_user(user)
        assert ids(get(desk, page_size=50)) == admin_ids
        assert desk.get("/api/inbox/ingested/summary").status_code == 200
    desk.cookies.clear()
    assert desk.get("/api/inbox/ingested").status_code == 401
    assert desk.get("/api/inbox/ingested/summary").status_code == 401


def test_summary_counts_add_up_to_the_list_totals(intake, mail, desk):
    body = desk.get("/api/inbox/ingested/summary").json()
    for status in ("ingested", "review", "failed", "ignored", "pending"):
        assert body[status] == len(expected(mail, status=status)) == get(desk, status=status)["total"], status
    assert sum(body[s] for s in ("ingested", "review", "failed", "ignored", "pending")) == 40
    created = len([row for row in mail if row["kind"] == "created"])
    assert body["createdCandidates"] == created == 8
    assert body["intake"] == {"ingestedCandidates": created, "review": body["review"], "accounts": 3}

    assert [a["address"] for a in body["accounts"]] == ["inbox-a@example.test", "inbox-b@example.test", "inbox-c@example.test"]
    for account in body["accounts"]:
        for status in ("ingested", "review", "failed", "ignored", "pending"):
            assert account[status] == len(expected(mail, account=account["id"], status=status)), (account["id"], status)
            assert account[status] == get(desk, account=account["id"], status=status)["total"]
    for status in ("ingested", "review", "failed", "ignored", "pending"):
        assert sum(a[status] for a in body["accounts"]) == body[status]
    gmail = next(a for a in body["accounts"] if a["id"] == "account-c")
    assert gmail["provider"] == "gmail" and gmail["autoImport"] is False and gmail["connectionStatus"] == "connected"
    assert set(gmail) == {"id", "address", "provider", "connectionStatus", "autoImport", "lastSyncAt", "lastError",
                          "ingested", "review", "failed", "ignored", "pending"}
    assert_no_private_data(body)


def test_summary_reports_mailbox_health(intake, mail, desk):
    with intake.sessions() as db:
        db.get(MailAccount, "account-a").last_error = "Login failed for token 1234567890123"
        db.get(MailAccount, "account-a").last_sync_at = NOW
        db.get(MailAccount, "account-b").disconnected_at = NOW
        db.commit()
    body = desk.get("/api/inbox/ingested/summary").json()
    states = {a["id"]: a for a in body["accounts"]}
    assert states["account-a"]["connectionStatus"] == "error" and "1234567890123" not in json.dumps(body)
    assert states["account-a"]["lastSyncAt"] and states["account-b"]["connectionStatus"] == "disconnected"
    assert body["intake"]["accounts"] == 2  # connected mailboxes only
    assert body["ingested"] == 12  # history of a disconnected mailbox still counts


def test_notes_never_carry_identity_numbers(intake, mail, desk):
    body = get(desk, page_size=50)
    text = json.dumps(body)
    for secret in ("N1234567", "9876543210", "1234 5678 9012", "5678 9012"):
        assert secret not in text
    redacted = next(item for item in body["items"] if "[hidden]" in (item["note"] or ""))
    assert redacted["note"] == "Passport N[hidden] and phone [hidden] or [hidden] do not agree." and redacted["status"] == "review"
    assert inbox_ingested.clean_text("Phone +91 98765 43210, Aadhaar 1234-5678-9012, passport A1234567, ok 12 Oct 2026.") == \
        "Phone +[hidden], Aadhaar [hidden], passport A[hidden], ok 12 Oct 2026."
    assert len(inbox_ingested.clean_text("word " * 100)) == 200
    assert inbox_ingested.clean_text("   ") is None and inbox_ingested.clean_text(None) is None
    # Raw OCR state never leaves the database.
    with intake.sessions() as db:
        job = db.get(EmailIngestionJob, mail[0]["job"])
        job.state = {"private": "raw-ocr-must-not-leave-database"}
        job.inspection = [{"private": "synthetic-only"}]
        db.commit()
    assert "raw-ocr-must-not-leave-database" not in json.dumps(get(desk, page_size=50))
    assert_no_private_data(body)


def test_list_and_summary_statement_counts_do_not_grow_with_mailboxes(intake, mail, desk):
    def measure():
        with recorded_statements(intake.engine) as page_statements:
            assert desk.get("/api/inbox/ingested", params={"page_size": 50}).status_code == 200
        with recorded_statements(intake.engine) as summary_statements:
            assert desk.get("/api/inbox/ingested/summary").status_code == 200
        with recorded_statements(intake.engine) as search_statements:
            assert desk.get("/api/inbox/ingested", params={"q": "welder", "status": "ingested", "account": "account-b"}).status_code == 200
        return len(page_statements), len(summary_statements), len(search_statements), page_statements, summary_statements

    page, summary, search, page_sql, summary_sql = measure()
    assert page <= 6 and summary <= 4 and search <= 6
    assert (page, summary) == (2, 4)  # the sign-in check plus one page statement; plus three side-by-side reads
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in page_sql + summary_sql)
    assert "email_attachments" in page_sql[-1]  # names, count and filed documents ride on the page statement
    assert "email_ingestion_jobs.state" not in page_sql[-1]  # deferred OCR state is never selected
    assert "LIMIT" in page_sql[-1].upper() and "OFFSET" in page_sql[-1].upper()

    with intake.sessions() as db:
        for n in range(7):
            db.add(MailAccount(id=f"account-extra-{n}", address=f"extra{n}@example.test", provider="imap", sync_state={}))
        db.commit()
    assert measure()[:3] == (page, summary, search)
    body = desk.get("/api/inbox/ingested/summary").json()
    assert len(body["accounts"]) == 10 and body["intake"]["accounts"] == 10


def test_a_page_past_the_end_costs_one_more_statement_only(intake, mail, desk):
    with recorded_statements(intake.engine) as statements:
        assert desk.get("/api/inbox/ingested", params={"page": 9, "page_size": 10}).json()["total"] == 40
    assert len(statements) == 3


def test_dashboard_inbox_card_numbers_ride_on_the_inbox_read_and_old_keys_stay(intake, mail, desk, monkeypatch):
    from app.api.routers import inbox
    monkeypatch.setattr(inbox, "get_settings", lambda: SimpleNamespace(email_ingestion_enabled=False))
    desk.as_user("attention-staff")  # staff: no credential names are read for them
    with recorded_statements(intake.engine) as statements:
        response = desk.get("/api/inbox", params={"limit": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["intake"] == {"ingestedCandidates": 8, "review": 8, "accounts": 3}
    assert body["intake"]["review"] == len(expected(mail, status="review")) == get(desk, status="review")["total"]
    assert {"accounts", "availableAccounts", "providers", "total", "offset", "limit", "hasMore", "scope", "messages", "jobs", "worker"} <= set(body)
    accounts = {a["id"]: a for a in body["accounts"]}
    assert set(accounts) == set(ACCOUNTS)
    assert {"id", "address", "provider", "messageCount", "unreadCount", "disconnectedAt", "connectionStatus", "autoIngest"} <= set(accounts["account-a"])
    assert accounts["account-a"]["messageCount"] == len([row for row in mail if row["account"] == "account-a"])
    assert len(statements) == 7  # the sign-in check plus the six side-by-side reads, exactly as before the card numbers were added
    assert sum("email_ingestion_jobs" in sql and "email_accounts" in sql and "count(*)" in sql.lower() for sql in statements) >= 1
