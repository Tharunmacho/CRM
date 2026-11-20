"""Removing an uploaded document or photo: a soft delete (migration 0054).

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL), with the application-form API fixture.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import event, inspect, select, text

from app.db.models import Application, Attachment, AuditEntry, Role
from app.services import application_form, candidate_exports, change_feed
from app.services.desk_snapshot import read_snapshot
from test_application_form import as_user, desk, form, upload, url  # noqa: F401  (fixtures and helpers)
from test_application_workflow import mapped
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

PNG = b"\x89PNG\r\n\x1a\n"


def image(tag: str) -> bytes:
    return PNG + f"synthetic image {tag}".encode()


def put(env, slot, tag, candidate="candidate"):
    response = upload(env, slot, image(tag), name=f"{slot}-{tag}.png", media="image/png", candidate=candidate)
    assert response.status_code in (200, 202), response.text
    return response.json()


def remove(env, attachment_id):
    return env.client.delete(f"/api/files/{attachment_id}")


def row_of(env, attachment_id):
    with env.sessions() as db:
        return db.get(Attachment, attachment_id)


def test_who_may_remove_a_file(desk):
    first = put(desk, "medical", "one")["document"]["current"]["attachmentId"]
    as_user(desk, "other", Role.staff)   # another recruiter, not the uploader, not the owner
    refused = remove(desk, first)
    assert refused.status_code == 403
    assert row_of(desk, first).deleted_at is None
    with desk.sessions() as db:   # ... but the person who uploaded a file may remove it
        db.add(Attachment(id="by-other", entity_type="candidate", entity_id="candidate", slot="other", version=1, file_name="x.pdf",
                          content_type="application/pdf", object_key="other-x.pdf", uploaded_by="other"))
        db.commit()
    assert remove(desk, "by-other").status_code == 200
    as_user(desk, "manager", Role.manager)
    assert remove(desk, first).status_code == 200   # an MD
    as_user(desk, "staff", Role.staff)   # the candidate's recruiter, who did not upload this one
    with desk.sessions() as db:
        db.add(Attachment(id="by-admin", entity_type="candidate", entity_id="candidate", slot="other", version=2, file_name="y.pdf",
                          content_type="application/pdf", object_key="other-y.pdf", uploaded_by="admin"))
        db.add(Attachment(id="for-admin", entity_type="candidate", entity_id="candidate", slot="other", version=3, file_name="z.pdf",
                          content_type="application/pdf", object_key="other-z.pdf", uploaded_by="admin"))
        db.commit()
    assert remove(desk, "by-admin").status_code == 200
    as_user(desk, "admin", Role.admin)
    assert remove(desk, "for-admin").status_code == 200
    assert remove(desk, "for-admin").status_code == 404   # already removed
    assert remove(desk, "no-such-file").status_code == 404


def test_removing_the_newest_version_falls_back_to_the_previous_one_and_keeps_everything(desk):
    one = put(desk, "medical", "one")["document"]["current"]
    two = put(desk, "medical", "two")["document"]["current"]
    assert form(desk)["documents"]["medical"]["current"]["attachmentId"] == two["attachmentId"]

    answer = desk.client.delete(url() + f"/documents/{two['attachmentId']}")
    assert answer.status_code == 200, answer.text
    medical = answer.json()["documents"]["medical"]
    assert medical["uploaded"] is True and medical["current"]["attachmentId"] == one["attachmentId"] and medical["versions"] == 1
    assert desk.client.get(f"/api/files/{two['attachmentId']}").status_code == 404
    assert [row["id"] for row in desk.client.get("/api/files", params={"entity_type": "candidate", "entity_id": "candidate"}).json()] == [one["attachmentId"]]

    with desk.sessions() as db:
        kept = db.get(Attachment, two["attachmentId"])
        assert kept is not None and kept.deleted_at is not None and kept.deleted_by == "staff" and kept.version == 2
        assert (desk.storage / kept.object_key).is_file()   # the bytes stay in storage
        entry = db.scalar(select(AuditEntry).where(AuditEntry.action == "Removed a document"))
        assert entry.target == kept.file_name and entry.actor_id == "staff" and entry.at is not None
        assert entry.entity_type == "candidate" and entry.entity_id == "candidate"
        assert entry.meta["slot"] == "medical" and entry.meta["attachment_id"] == two["attachmentId"] and entry.meta["version"] == 2
        assert entry.meta["removed_by"] == "staff" and entry.meta["removed_at"]
        assert "medical" not in {gap["slot"] for gap in application_form.missing_required_documents(db, "candidate")}

    assert desk.client.delete(url() + f"/documents/{one['attachmentId']}").status_code == 200
    gone = form(desk)["documents"]["medical"]
    assert gone["uploaded"] is False and gone["current"] is None
    with desk.sessions() as db:
        assert "medical" in {gap["slot"] for gap in application_form.missing_required_documents(db, "candidate")}
        assert candidate_exports.load_inspection(db, "candidate", desk.actor).attachments == []   # not in the record package either

    again = put(desk, "medical", "two")   # the same bytes can be uploaded again after a removal: a new version, not "already uploaded"
    assert not again.get("alreadyUploaded") and again["document"]["current"]["version"] == 3


def test_removing_the_photo_returns_the_placeholder(desk):
    photo = put(desk, "photo", "face")["document"]["current"]
    assert form(desk)["photo"]["attachmentId"] == photo["attachmentId"]
    assert desk.client.delete(url() + f"/documents/{photo['attachmentId']}").status_code == 200
    assert form(desk)["photo"] is None


def test_the_form_route_only_removes_files_of_its_own_candidate(desk):
    mine = put(desk, "medical", "mine")["document"]["current"]["attachmentId"]
    assert desk.client.delete(url("second-candidate") + f"/documents/{mine}").status_code == 404
    assert row_of(desk, mine).deleted_at is None


def test_a_removed_file_does_not_block_the_same_bytes_for_another_candidate_and_leaves_the_counts(desk):
    put(desk, "photo", "same")
    clash = upload(desk, "photo", image("same"), name="p.png", media="image/png", candidate="second-candidate")
    assert clash.status_code == 409   # one person's photo is not filed twice
    with desk.sessions() as db:
        counts = {row["id"]: row["document_counts"] for row in read_snapshot(db, "admin", compact=True, slim=True)["candidates"]}
        assert counts["candidate"] == {"photo": 1}
    first = form(desk)["photo"]["attachmentId"]
    assert remove(desk, first).status_code == 200
    with desk.sessions() as db:
        slim = {row["id"]: row["document_counts"] for row in read_snapshot(db, "admin", compact=True, slim=True)["candidates"]}
        full = read_snapshot(db, "admin")
        assert slim["candidate"] == {}
        assert [f for f in full["files"] if f["entity_id"] == "candidate"] == []
    assert upload(desk, "photo", image("same"), name="p.png", media="image/png", candidate="second-candidate").status_code in (200, 202)


def test_removing_a_file_also_drops_the_stage_draft_pointer_to_it(desk):
    app_id = mapped(desk)
    with desk.sessions() as db:
        db.add(Attachment(id="passport-file", entity_type="candidate", entity_id="candidate", slot="passport", version=1, file_name="p.pdf",
                          content_type="application/pdf", object_key="p.pdf", uploaded_by="staff"))
        application = db.get(Application, app_id)
        application.draft = {"copy": {"id": "passport-file", "name": "p.pdf", "url": "/api/files/passport-file"}, "other": "kept"}
        db.commit()
    assert remove(desk, "passport-file").status_code == 200
    with desk.sessions() as db:
        assert db.get(Application, app_id).draft == {"other": "kept"}


def test_removing_a_file_is_two_writes_announces_the_document_topics_and_never_hard_deletes(desk):
    target = put(desk, "medical", "budget")["document"]["current"]["attachmentId"]
    seen: list[str] = []
    engine = desk.sessions.kw["bind"]

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert remove(desk, target).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)
    writes = [s for s in seen if s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
    assert len(writes) == 2 and len(seen) <= 5, seen   # one UPDATE of the file, one audit entry; one read before them
    assert not any(s.lstrip().upper().startswith("DELETE") for s in seen)
    update = next(s for s in writes if s.upper().startswith("UPDATE"))
    assert change_feed.classify_statement(update) == ("UPDATE", "document_attachments")   # the live topics of an upload
    assert change_feed.TOPIC_TABLES["document_attachments"] == ("candidates", "documents")


def test_migration_0054_adds_the_columns_idempotently_and_reverses(schema_database):
    engine = schema_database
    _migrate(engine, "0052_speed_indexes_tasks")
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO document_attachments (id,entity_type,entity_id,slot,version,file_name,object_key) "
                                "VALUES ('a','candidate','c','medical',1,'m.pdf','k')"))
    _migrate(engine, "head")
    attachments = {c["name"]: c for c in inspect(engine).get_columns("document_attachments")}
    feedback = {c["name"]: c for c in inspect(engine).get_columns("crm_application_feedback")}
    assert {"deleted_at", "deleted_by"} <= set(attachments) and {"deleted_at", "deleted_by", "edited_at"} <= set(feedback)
    assert all(attachments[name]["comment"] for name in ("deleted_at", "deleted_by"))
    assert all(feedback[name]["comment"] for name in ("deleted_at", "deleted_by", "edited_at"))
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT deleted_at FROM document_attachments WHERE id='a'")) is None
        connection.execute(text("UPDATE alembic_version SET version_num = '0052_speed_indexes_tasks'"))
    _migrate(engine, "head")   # a repeat over its own columns is harmless and keeps the rows
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM document_attachments")) == 1
    _migrate(engine, "0052_speed_indexes_tasks", downgrade=True)
    assert "deleted_at" not in {c["name"] for c in inspect(engine).get_columns("document_attachments")}
    assert "edited_at" not in {c["name"] for c in inspect(engine).get_columns("crm_application_feedback")}
    _migrate(engine, "head")
    assert "deleted_by" in {c["name"] for c in inspect(engine).get_columns("document_attachments")}
