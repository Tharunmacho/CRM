"""Full-response parity against the original report reader on generated records."""
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db import models as m
from app.services.reporting import Filters, read_reporting_data, report_data
from test_database_naming import _migrate, schema_database
from test_reporting import ADMIN, DAY, MANAGER, NOW, STAFF, dataset


def test_sql_projection_preserves_full_reports_across_roles_dates_and_relations(schema_database, dataset):
    _migrate(schema_database, "head")
    with Session(schema_database) as db:
        for row in dataset["users"]:
            db.add(m.User(**row, email=row["id"] + "@example.test"))
        db.flush()
        for key, model in [("clients", m.Client), ("companies", m.Company), ("designations", m.Designation),
                           ("orders", m.Order), ("candidates", m.Candidate), ("applications", m.Application),
                           ("stages", m.ApplicationStage), ("tasks", m.Task)]:
            db.add_all(model(**row, **({"kind": "stage" if row["stage"] else "initial_call"} if key == "tasks" else {}))
                       for row in dataset[key]); db.flush()
        db.add_all(m.TaskEvent(**row, event_key=row["id"]) for row in dataset["events"])
        db.add_all(m.Remark(**row, body="Daily update") for row in dataset["remarks"])
        # A future effective receipt supersedes an in-range receipt: never filter
        # attempts by date before deciding which attempt is current.
        db.add(m.ApplicationStage(id="later-receipt", application_id="app1", stage=8, attempt=2,
            data={"amount": "200.50", "currency": "USD", "documents": [{"ignored": "large payload" * 100}]},
            owner_id="a", completed_by="admin", completed_at=NOW + timedelta(days=1)))
        # Latest reopened attempt must not hide the earlier valid legacy receipt.
        db.add(m.ApplicationStage(id="legacy-receipt", application_id="app6", stage=8, attempt=2,
            data={"amount": "35.75", "currency": "EUR"}, owner_id=None, completed_by="admin", completed_at=NOW))
        db.add(m.ApplicationStage(id="reopened-later", application_id="app6", stage=8, attempt=3,
            data={"amount": 999, "currency": "INR"}, owner_id="b", completed_by="admin", completed_at=NOW,
            reopened_reason="Correction"))
        # Stage duration uses the matching execution even if its current owner
        # differs from the historical stage owner; pruning by task owner is wrong.
        db.add(m.Task(id="duration-task", kind=m.TaskKind.stage, title="Receipt", candidate_id="c6",
            application_id="app6", stage=8, owner_id="b", created_at=NOW - timedelta(hours=3), completed_at=NOW))
        db.add(m.MailAccount(id="mailbox", address="original@example.test", provider="imap", auto_ingest=False))
        db.add(m.Candidate(id="old-intake", ref="OLD", name="Old intake", phone="9000000099", source=m.Source.email,
            owner_id="a", intake_owner_id="a", created_at=NOW - timedelta(days=365), source_detail="email_message:old-message"))
        db.flush()
        db.get(m.Candidate, "c1").source_detail = "email_message:origin"
        for mid in ("origin", "unrelated", "old-message"):
            db.add(m.MailMessage(id=mid, account_id="mailbox", provider_message_id=mid,
                received_at=NOW - timedelta(days=365), subject="Synthetic provenance"))
        db.commit()
        original = read_reporting_data(db)
        periods = [(DAY, DAY), (DAY - timedelta(days=25), DAY), (DAY + timedelta(days=1), DAY + timedelta(days=1)),
                   (date(2026, 11, 1), date(2026, 11, 1))]
        cases = 0
        for user in (STAFF, MANAGER, ADMIN):
            for first, last in periods:
                for relation in ({}, {"client_id": "cl1"}, {"order_id": "o2"}, {"designation_id": "d1"}, {"staff_id": "a"}):
                    filters = Filters(first, last, **relation).authorize(user)
                    projected = read_reporting_data(db, filters)
                    assert report_data(projected, user, filters, now=NOW) == report_data(original, user, filters, now=NOW)
                    cases += 1
        assert cases == 60
        today = read_reporting_data(db, Filters(DAY, DAY))
        assert "t3" in {row["id"] for row in today["tasks"]}  # Old task with an in-period event.
        assert "later-receipt" in {row["id"] for row in today["stages"]}  # Effective before date filtering.
        assert "reopened-later" not in {row["id"] for row in today["stages"]}
        assert today["mailSources"] == [{"id": "origin", "address": "original@example.test"}]
