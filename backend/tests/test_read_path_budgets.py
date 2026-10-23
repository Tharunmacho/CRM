"""Round-trip budgets for the hot read paths.

The production database is a network hop away (150-350 ms per round trip), so these tests pin the
number of SQL statements (and which session type serves a request), not wall time.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.db.models import MailAttachment, MailMessage, Role, User
from app.integrations.mail.mime import parse_message
from test_database_naming import schema_database  # noqa: F401
from test_workflow_scheduler import workspace  # noqa: F401

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=timezone.utc)


@contextmanager
def statements_of(engine):
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def test_get_requests_use_autocommit_sessions_and_writes_keep_transactions():
    # Importing the session module must not need the real database settings.
    with patch("app.core.config.get_settings", return_value=SimpleNamespace(
            database_url="postgresql+psycopg://reader@127.0.0.1:1/unused")):
        from app.db.session import get_db, writes_on_get

    app = FastAPI()

    def isolation(db):
        return db.get_bind().get_execution_options().get("isolation_level", "default")

    @app.get("/read")
    def read(db=Depends(get_db)):
        return {"isolation": isolation(db)}

    @app.get("/changes")
    @writes_on_get
    def changes(db=Depends(get_db)):
        return {"isolation": isolation(db)}

    @app.post("/write")
    def write(db=Depends(get_db)):
        return {"isolation": isolation(db)}

    client = TestClient(app)
    assert client.get("/read").json() == {"isolation": "AUTOCOMMIT"}
    assert client.get("/changes").json() == {"isolation": "default"}
    assert client.post("/write").json() == {"isolation": "default"}


def test_pool_counts_is_one_statement(workspace):
    from app.services.candidate_search import pool_counts

    with workspace() as db, statements_of(db.get_bind()) as seen:
        counts = pool_counts(db)
    assert len(seen) == 1 and seen[0].upper().startswith("SELECT")
    assert set(counts) == {"all", "unmapped", "not_interested", "unassigned", "needs_review"}
    assert counts["all"] == 1  # the fixture's single candidate


def test_candidate_list_reads_count_and_page_but_never_resume_text(workspace):
    from app.services.candidate_search import Search, search_candidates

    with workspace() as db:
        with statements_of(db.get_bind()) as seen:
            result = search_candidates(db, Search(), 0, 20)
    assert len(seen) == 2
    assert not any("resume_text" in sql.split(" FROM ")[0] for sql in seen if "count(" not in sql.lower())
    assert result["total"] == 1 and result["items"][0]["resumeText"] == ""
    with workspace() as db:  # a page past the end falls back to the last page
        assert search_candidates(db, Search(), 9, 20)["page"] == 0


def test_attention_list_runs_its_six_reads_together_and_keeps_totals(workspace):
    from app.services.intake_attention import attention_list

    with workspace() as db:
        admin = db.get(User, "admin")
        with statements_of(db.get_bind()) as seen:
            result = attention_list(db, admin)
    assert len(seen) == 6
    assert result["totals"] == {"email": 0, "whatsapp": 0, "manual": 0, "all": 0}
    assert result["items"] == [] and result["canRetry"] is True


def incoming(number):
    raw = (f"From: Sender {number} <sender{number}@example.test>\r\nSubject: Message {number}\r\n\r\nBody {number}").encode()
    return parse_message(raw, provider_id=f"page:{number}", uid=number, uid_validity="1", folder="INBOX",
                         received_at=NOW, unread=True)


def test_persisting_a_mailbox_page_costs_the_same_statements_for_any_page_size(workspace):
    from app.services.mailbox_sync import persist_messages
    from app.db.models import MailAccount

    with workspace() as db:
        db.add(MailAccount(id="page-account", address="page@example.test", provider="zoho", sync_state={}))
        db.commit()
    costs = {}
    for size, start in ((2, 0), (12, 100)):
        with workspace() as db, statements_of(db.get_bind()) as seen:
            rows = persist_messages(db, None, "page-account", [incoming(start + n) for n in range(size)])
            db.commit()
        costs[size] = len(seen)
        assert len({row.id for row in rows}) == size
    assert costs[2] == costs[12]
    # Viewing the same page again updates the rows rather than duplicating them.
    with workspace() as db:
        again = persist_messages(db, None, "page-account", [incoming(100 + n) for n in range(12)])
        db.commit()
        assert len(again) == 12
        assert len(list(db.scalars(select(MailMessage).where(MailMessage.account_id == "page-account")))) == 14
        assert list(db.scalars(select(MailAttachment))) == []


def test_attendance_report_reads_people_shifts_and_presence_together(workspace):
    from app.services import staff_activity

    with workspace() as db:
        admin = db.get(User, "admin")
        with statements_of(db.get_bind()) as seen:
            report = staff_activity.attendance_report(db, admin, NOW.date(), "Asia/Kolkata", at=NOW)
    assert len(seen) == 3 and all(sql.upper().startswith("SELECT") for sql in seen)
    assert {item["userId"] for item in report["staff"]} == {"owner", "other", "manager"}
    assert all(item["role"] in {Role.staff.value, Role.manager.value} for item in report["staff"])
