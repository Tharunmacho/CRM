"""Keep the desk snapshot complete, private, fresh, and bounded to one data query.

PostgreSQL cases reuse the existing guarded crm_schema_test* fixture. They never
read backend/.env and skip unless an explicit disposable TEST_DATABASE_URL is set.
"""

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from app.api.schemas import (
    ApplicationOut, CandidateOut, ClientOut, CompanyOut, DesignationOut,
    NotificationOut, OrderOut, RemarkOut, StageRecordOut, TaskOut, TodoOut, UserOut,
)
from app.core.security import SESSION_COOKIE
from app.db.models import (
    Application, ApplicationStage, Attachment, Candidate, Client, Company,
    Designation, Notification, Order, Remark, Session as SessionRow, Task,
    TodoItem, User,
)
from app.services.desk_snapshot import read_snapshot, snapshot_statement
from test_database_naming import (
    PREVIOUS, RENAMED, _migrate, _seed_previous_schema, schema_database,
)


STAMP = datetime(2026, 1, 10, 9, 30, tzinfo=timezone.utc)
SNAPSHOT_COLLECTIONS = {
    "users", "clients", "companies", "designations", "orders", "candidates",
    "files", "agreements", "applications", "stages", "tasks", "remarks",
    "todos", "notifications",
}
WIRE_COLLECTIONS = SNAPSHOT_COLLECTIONS - {"files", "agreements", "stages"}
FORBIDDEN_FIELDS = {
    "password_hash", "passwordHash", "object_key", "objectKey",
    "content_hash", "contentHash", "ocr_data", "ocrData",
    "resume_tsv", "resumeTsv", "secrets",
}


@contextmanager
def recorded_statements(engine):
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        # Only SQL shape is retained, never bound credentials or row contents.
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def assert_select_budget(statements, expected):
    assert len(statements) == expected
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in statements)


def test_snapshot_compilation_uses_an_explicit_public_projection():
    statement = snapshot_statement("projection-only-user")
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert set(statement.selected_columns.keys()) == SNAPSHOT_COLLECTIONS
    assert "projection-only-user" not in sql
    assert "projection-only-user" in compiled.params.values()
    assert "SELECT *" not in sql.upper()
    for field in FORBIDDEN_FIELDS:
        assert field not in sql
    for private_table in (
        "auth_sessions", "email_accounts", "email_messages", "email_attachments",
        "ingestion_failures", "system_audit_log",
    ):
        assert private_table not in sql
    assert "personal_todos.owner_id =" in sql
    assert "system_notifications.user_id =" in sql
    # Large raw OCR/search columns are omitted while the supported resume and
    # workflow draft fields remain available to the existing desk.
    assert "crm_candidates.resume_text" in sql
    assert "crm_applications.draft" in sql


def enrich_fixture(engine):
    with engine.begin() as connection:
        connection.execute(
            User.__table__.update().where(User.id == "schema-user").values(
                phone="+910000000010"
            )
        )
        connection.execute(
            User.__table__.update().where(User.id == "schema-staff").values(
                active=True, name="Other recruiter"
            )
        )
        connection.execute(
            Client.__table__.update().values(
                phone="+910000000011", whatsapp="+910000000012",
                country="India", city="Chennai", notes="Contact notes",
            )
        )
        connection.execute(
            Company.__table__.update().values(
                country="UAE", city="Dubai", industry="Fabrication",
                contact_person="Synthetic contact", contact_email="office@example.test",
                phone="+971000000000", address="Synthetic address", notes="Employer notes",
            )
        )
        connection.execute(
            Designation.__table__.update().values(
                category="Trade", description="Synthetic designation description"
            )
        )
        connection.execute(
            Order.__table__.update().values(
                country="UAE", city="Dubai", salary=1450.25, benefits=["Housing"],
                contract_period="24 months", interview_date=date(2026, 2, 3),
                deadline=date(2026, 2, 28),
            )
        )
        connection.execute(
            Candidate.__table__.update().values(
                whatsapp="+910000000013", dob=date(1990, 3, 4), gender="male",
                nationality="Indian", current_city="Chennai", languages=["Tamil", "English"],
                passport_no="SYNTHETIC001", passport_issue=date(2024, 1, 2),
                passport_expiry=date(2034, 1, 1), passport_place="Chennai",
                designations=["Welder"], total_experience=8, gulf_experience=3,
                expected_salary=1750.75, source_detail="Synthetic inbox",
                needs_review=True, created_at=STAMP, call_called=True,
                call_interested="yes", call_remarks="Available next month",
                call_recorded_at=STAMP,
                ocr_data={"private": "raw-ocr-must-not-leave-database"},
            )
        )
        connection.execute(
            Attachment.__table__.update().values(size_kb=125, uploaded_at=STAMP)
        )
        connection.execute(
            Attachment.__table__.insert(),
            [
                {
                    "id": f"agreement-v{version}", "entity_type": "company",
                    "entity_id": "schema-company", "slot": "agreement", "version": version,
                    "file_name": f"agreement-v{version}.pdf",
                    "object_key": f"private-verification/agreement-v{version}.pdf",
                    "content_hash": str(version) * 64, "size_kb": 60 + version,
                    "uploaded_by": "schema-user", "uploaded_at": STAMP,
                }
                for version in (3, 1, 2)
            ] + [{
                "id": "candidate-passport", "entity_type": "candidate",
                "entity_id": "schema-candidate", "slot": "passport", "version": 1,
                "file_name": "synthetic-passport.pdf",
                "object_key": "private-verification/passport.pdf",
                "content_hash": "d" * 64, "size_kb": 200,
                "uploaded_by": "schema-staff", "uploaded_at": STAMP,
            }],
        )
        connection.execute(
            ApplicationStage.__table__.insert(),
            [
                {
                    "id": f"stage-{stage}-{attempt}",
                    "application_id": "schema-application", "stage": stage,
                    "attempt": attempt, "data": {"attempt": attempt, "stage": stage},
                    "outcome": "passed", "completed_by": "schema-user",
                    "completed_at": STAMP + timedelta(hours=stage, minutes=attempt),
                    "amount": 250.50, "amount_currency": "INR",
                    "reopened_reason": "Internal stage metadata",
                }
                for stage, attempt in ((2, 2), (1, 2), (2, 1))
            ],
        )
        connection.execute(
            TodoItem.__table__.update().values(
                notes="Personal reminder details", link_to="/candidates/schema-candidate",
                link_label="Open candidate",
            )
        )
        connection.execute(
            TodoItem.__table__.insert().values(
                id="other-todo", owner_id="schema-staff", title="Other user's reminder",
                due_at=STAMP, priority="low", done=True,
            )
        )
        connection.execute(
            Notification.__table__.update().values(
                created_at=STAMP, detail="Assignment detail", link_to="/tasks"
            )
        )
        connection.execute(
            Notification.__table__.insert(),
            [
                {
                    "id": "manager-new-notification", "user_id": "schema-user",
                    "kind": "reminder", "title": "New manager notification",
                    "created_at": STAMP + timedelta(hours=1), "read": True,
                },
                {
                    "id": "other-notification", "user_id": "schema-staff",
                    "kind": "assignment", "title": "Other user's notification",
                    "created_at": STAMP, "read": False,
                },
            ],
        )
        connection.execute(
            SessionRow.__table__.insert().values(
                id="other-existing-session", user_id="schema-staff",
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )


@pytest.fixture
def desk_engine(schema_database):
    _migrate(schema_database, PREVIOUS)
    _seed_previous_schema(schema_database)
    _migrate(schema_database, RENAMED)
    enrich_fixture(schema_database)
    return schema_database


@pytest.fixture
def desk_client(desk_engine):
    # A small isolated app has no startup hooks, Redis, static files, or .env reads.
    with patch(
        "app.core.config.get_settings",
        return_value=SimpleNamespace(
            database_url=desk_engine.url.render_as_string(hide_password=False)
        ),
    ):
        from app.api.routers import auth, data
        from app.db.session import get_db

    application = FastAPI()
    application.include_router(auth.router)
    application.include_router(data.router)
    factory = sessionmaker(bind=desk_engine, expire_on_commit=False)

    def isolated_database():
        with factory() as db:
            yield db

    application.dependency_overrides[get_db] = isolated_database
    with TestClient(application, base_url="https://testserver") as client:
        client.cookies.set(SESSION_COOKIE, "schema-existing-session")
        yield client


def wire(row, schema):
    return schema.model_validate(row).model_dump(by_alias=True, mode="json")


def by_id(rows):
    return {row["id"]: row for row in rows}


def assert_no_private_data(value):
    if isinstance(value, dict):
        assert not FORBIDDEN_FIELDS.intersection(value)
        for child in value.values():
            assert_no_private_data(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_private_data(child)
    serialized = json.dumps(value)
    for marker in (
        "$argon2", "schema-existing-session", "other-existing-session",
        "private-verification/", "test/resume/", "raw-ocr-must-not-leave-database",
        "synthetic-only", "b" * 64, "c" * 64,
    ):
        assert marker not in serialized


def test_bootstrap_preserves_full_wire_contract_with_two_database_queries(
    desk_engine, desk_client
):
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get("/api/bootstrap")
    assert response.status_code == 200
    assert_select_budget(statements, 2)
    body = response.json()
    assert set(body) == WIRE_COLLECTIONS | {"me"}
    assert_no_private_data(body)

    # Compare all existing direct fields against the previous ORM/schema path,
    # independently of the snapshot query's field selection.
    with Session(desk_engine) as db:
        assert body["me"] == wire(db.get(User, "schema-user"), UserOut)
        for key, model, schema in (
            ("users", User, UserOut), ("clients", Client, ClientOut),
            ("designations", Designation, DesignationOut), ("orders", Order, OrderOut),
            ("tasks", Task, TaskOut), ("remarks", Remark, RemarkOut),
        ):
            expected = [wire(row, schema) for row in db.scalars(select(model))]
            assert by_id(body[key]) == by_id(expected)
        for key, model, schema, owner_column in (
            ("todos", TodoItem, TodoOut, TodoItem.owner_id),
            ("notifications", Notification, NotificationOut, Notification.user_id),
        ):
            expected = [
                wire(row, schema)
                for row in db.scalars(select(model).where(owner_column == "schema-user"))
            ]
            assert by_id(body[key]) == by_id(expected)

        candidate = body["candidates"][0]
        expected_candidate = wire(db.get(Candidate, "schema-candidate"), CandidateOut)
        computed = {"addedAt", "documents", "callOutcome"}
        assert {k: v for k, v in candidate.items() if k not in computed} == {
            k: v for k, v in expected_candidate.items() if k not in computed
        }
        assert candidate["addedAt"] == "2026-01-10T09:30:00Z"
        assert candidate["callOutcome"] == {
            "called": True, "interested": "yes", "remarks": "Available next month",
            "recordedAt": "2026-01-10T09:30:00Z",
        }
        slots = {slot["key"]: slot for slot in candidate["documents"]}
        assert list(slots) == ["resume", "passport", "photo", "education", "experience"]
        assert slots["resume"]["label"] == "Resume"
        assert slots["resume"]["versions"] == [
            {
                "id": f"schema-file-v{version}", "name": f"synthetic-cv-v{version}.pdf",
                "sizeKb": 125, "version": version,
                "url": f"/api/files/schema-file-v{version}", "uploadedBy": "schema-user",
                "uploadedAt": "2026-01-10T09:30:00Z",
            }
            for version in (1, 2)
        ]
        assert slots["passport"]["versions"][0]["id"] == "candidate-passport"
        assert all(slots[key]["versions"] == [] for key in ("photo", "education", "experience"))

        company = body["companies"][0]
        expected_company = wire(db.get(Company, "schema-company"), CompanyOut)
        assert {k: v for k, v in company.items() if k != "agreement"} == {
            k: v for k, v in expected_company.items() if k != "agreement"
        }
        assert company["agreement"] == {
            "id": "agreement-v3", "name": "agreement-v3.pdf",
            "url": "/api/files/agreement-v3",
        }
        application = body["applications"][0]
        expected_application = wire(db.get(Application, "schema-application"), ApplicationOut)
        assert {k: v for k, v in application.items() if k != "stages"} == {
            k: v for k, v in expected_application.items() if k != "stages"
        }
        expected_stages = [
            wire(row, StageRecordOut)
            for row in db.scalars(
                select(ApplicationStage).order_by(
                    ApplicationStage.stage, ApplicationStage.attempt
                )
            )
        ]
        assert application["stages"] == expected_stages
        assert [(row["stage"], row["attempt"]) for row in application["stages"]] == [
            (1, 1), (1, 2), (2, 1), (2, 2)
        ]
    assert [row["id"] for row in body["notifications"]] == [
        "manager-new-notification", "schema-notification"
    ]


def test_bootstrap_is_protected_before_any_desk_query(desk_engine, desk_client):
    desk_client.cookies.clear()
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get("/api/bootstrap")
    assert response.status_code == 401
    assert statements == []

    desk_client.cookies.set(SESSION_COOKIE, "not-a-real-session")
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get("/api/bootstrap")
    assert response.status_code == 401
    assert_select_budget(statements, 1)
    assert "json_agg" not in statements[0].lower()


def test_bootstrap_isolates_personal_data_for_each_current_user(desk_engine, desk_client):
    for token, user_id, todo_ids, notification_ids in (
        (
            "schema-existing-session", "schema-user", {"schema-todo"},
            {"schema-notification", "manager-new-notification"},
        ),
        (
            "other-existing-session", "schema-staff", {"other-todo"},
            {"other-notification"},
        ),
        (
            "schema-existing-session", "schema-user", {"schema-todo"},
            {"schema-notification", "manager-new-notification"},
        ),
    ):
        desk_client.cookies.set(SESSION_COOKIE, token)
        with recorded_statements(desk_engine) as statements:
            response = desk_client.get("/api/bootstrap")
        assert response.status_code == 200
        assert_select_budget(statements, 2)
        body = response.json()
        assert body["me"]["id"] == user_id
        assert set(by_id(body["todos"])) == todo_ids
        assert set(by_id(body["notifications"])) == notification_ids
        assert_no_private_data(body)


def test_empty_snapshot_returns_arrays_with_one_query(schema_database):
    _migrate(schema_database, RENAMED)
    with Session(schema_database) as db:
        with recorded_statements(schema_database) as statements:
            snapshot = read_snapshot(db, "no-account")
    assert_select_budget(statements, 1)
    assert snapshot == {key: [] for key in SNAPSHOT_COLLECTIONS}


def test_snapshot_query_count_stays_constant_and_committed_changes_are_visible(desk_engine):
    with Session(desk_engine) as db:
        with recorded_statements(desk_engine) as statements:
            initial = read_snapshot(db, "schema-user")
        assert_select_budget(statements, 1)
        assert len(initial["candidates"]) == 1

        with desk_engine.begin() as connection:
            connection.execute(
                Candidate.__table__.update()
                .where(Candidate.id == "schema-candidate")
                .values(name="Updated persisted name")
            )
            connection.execute(
                Candidate.__table__.insert(),
                [
                    {
                        "id": f"extra-candidate-{index:02}", "ref": f"EXTRA-{index:02}",
                        "name": f"Extra candidate {index}", "phone": f"+910000001{index:02}",
                        "source": "walk_in", "owner_id": "schema-staff",
                        "created_at": STAMP + timedelta(days=1),
                    }
                    for index in range(24)
                ],
            )
            connection.execute(
                Attachment.__table__.insert(),
                [
                    {
                        "id": f"extra-file-{index:02}", "entity_type": "candidate",
                        "entity_id": f"extra-candidate-{index:02}", "slot": "resume",
                        "version": 1, "file_name": "extra-cv.pdf",
                        "object_key": f"private-verification/extra-{index:02}.pdf",
                    }
                    for index in range(24)
                ],
            )
            connection.execute(
                TodoItem.__table__.update()
                .where(TodoItem.id == "schema-todo").values(done=True)
            )
        with recorded_statements(desk_engine) as statements:
            updated = read_snapshot(db, "schema-user")
        assert_select_budget(statements, 1)

    assert len(updated["candidates"]) == 25
    assert len(updated["files"]) == 27
    assert [row["id"] for row in updated["candidates"][:24]] == [
        f"extra-candidate-{index:02}" for index in range(24)
    ]
    assert by_id(updated["candidates"])["schema-candidate"]["name"] == "Updated persisted name"
    assert by_id(updated["todos"])["schema-todo"]["done"] is True
    assert by_id(initial["todos"])["schema-todo"]["done"] is False
    assert_no_private_data(updated)


def test_bootstrap_accepts_legacy_call_outcome_without_recorded_time(desk_engine, desk_client):
    with desk_engine.begin() as connection:
        connection.execute(
            Candidate.__table__.update()
            .where(Candidate.id == "schema-candidate")
            .values(call_recorded_at=None)
        )
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get("/api/bootstrap")
    assert response.status_code == 200
    assert_select_budget(statements, 2)
    assert response.json()["candidates"][0]["callOutcome"] == {
        "called": True, "interested": "yes", "remarks": "Available next month",
        "recordedAt": None,
    }
