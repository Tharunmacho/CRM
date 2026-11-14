"""Manual checklist confirmations are audit records, never fake file or invoice evidence."""
from io import BytesIO
import json
from zipfile import ZipFile

import pytest
from sqlalchemy import func, select

from app.api.routers import candidates as candidate_routes
from app.db.models import Attachment, AuditEntry, CandidateInvoice, DocumentExtractionJob, Role
from app.services import application_form, candidate_search
from app.services.desk_snapshot import read_snapshot
from app.services.document_status import ACTION, statuses_by_candidate
from test_application_form import as_user, desk, form
from test_application_workflow import mapped
from test_bootstrap_performance import recorded_statements
from test_candidate_exports import add_file, exports, get, sheets
from test_change_feed import Listener
from test_database_naming import schema_database


def mark(env, slot="passport", uploaded=True, note="Received the paper document", **params):
    return env.client.put(f"/api/candidates/candidate/application-form/documents/{slot}/status",
                          params=params, json={"uploaded": uploaded, "note": note})


def count_marks(env):
    with env.sessions() as db:
        return db.scalar(select(func.count()).select_from(AuditEntry).where(AuditEntry.action == ACTION))


def test_manual_mark_persists_counts_completeness_and_retains_history_without_files(desk):
    before = form(desk)
    response = mark(desk)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    data = response.json()
    passport = data["documents"]["passport"]
    assert passport["uploaded"] and not passport["fileUploaded"]
    assert passport["current"] is None and passport["files"] == [] and passport["versions"] == 0
    assert passport["manualStatus"]["by"] == {"id": "staff", "name": "Staff"}
    assert passport["manualStatus"]["note"] == "Received the paper document" and passport["manualStatus"]["at"]
    assert data["completeness"]["filled"] == before["completeness"]["filled"] + 1
    assert form(desk)["documents"]["passport"] == passport
    assert mark(desk).status_code == 200 and count_marks(desk) == 1
    cleared = mark(desk, uploaded=False, note="Document returned for correction").json()["documents"]["passport"]
    assert not cleared["uploaded"] and not cleared["fileUploaded"] and not cleared["manualStatus"]["uploaded"]
    assert count_marks(desk) == 2
    with desk.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
        assert db.scalar(select(func.count()).select_from(DocumentExtractionJob)) == 0
        assert db.scalar(select(func.count()).select_from(CandidateInvoice)) == 0
        history = db.scalars(select(AuditEntry).where(AuditEntry.action == ACTION).order_by(AuditEntry.at)).all()
        assert [row.meta["uploaded"] for row in history] == [True, False]


def test_manual_air_ticket_or_invoice_never_generates_invoice_or_ocr(desk):
    mapped(desk)
    for slot in ("air_ticket", "resume"):
        assert mark(desk, slot=slot).status_code == 200
    assert mark(desk, slot="invoice").status_code == 403  # the Invoice document is the admin's alone
    as_user(desk, "admin", Role.admin)
    assert mark(desk, slot="invoice").status_code == 200
    with desk.sessions() as db:
        for model in (Attachment, DocumentExtractionJob, CandidateInvoice):
            assert db.scalar(select(func.count()).select_from(model)) == 0


def test_real_file_cannot_be_marked_pending_and_supersedes_manual_mark(exports):
    assert mark(exports).status_code == 200
    add_file(exports, "actual-passport", "passport")
    data = form(exports)["documents"]["passport"]
    assert data["uploaded"] and data["fileUploaded"] and data["current"]["url"] == "/api/files/actual-passport"
    assert data["manualStatus"]["uploaded"]
    denied = mark(exports, uploaded=False)
    assert denied.status_code == 409 and "file is attached" in denied.json()["detail"]
    assert count_marks(exports) == 1
    assert mark(exports, note="Different note").status_code == 200
    assert count_marks(exports) == 1
    add_file(exports, "legacy-school", "education")
    assert mark(exports, slot="education_certificate", uploaded=False).status_code == 409


def test_ownership_context_and_strict_boolean_are_checked_before_status_write(desk):
    other = mapped(desk, candidate="second-candidate")
    assert mark(desk, applicationId=other).status_code == 404
    assert mark(desk, slot="unknown").status_code == 422
    response = desk.client.put("/api/candidates/candidate/application-form/documents/passport/status", json={"uploaded": "true"})
    assert response.status_code == 422
    as_user(desk, "other", Role.staff)
    assert mark(desk).status_code == 403
    assert count_marks(desk) == 0
    as_user(desk, "manager", Role.manager)
    assert mark(desk).status_code == 200


def test_exports_distinguish_manual_confirmation_and_do_not_invent_zip_files(exports):
    assert mark(exports, note="Checked paper copy =1+1").status_code == 200
    listing_response = get(exports, "documents.zip")
    assert listing_response.status_code == 200, listing_response.text
    with ZipFile(BytesIO(listing_response.content)) as archive:
        listing = json.loads(archive.read("manifest.json"))
        passport = next(row for row in listing["checklist"] if row["slot"] == "passport")
        assert passport["status"] == "Marked manually (no file attached)"
        assert not passport["fileUploaded"] and passport["current"] is None
        assert passport["manualStatus"]["note"] == "Checked paper copy =1+1"
        assert listing["documents"] == []
        assert len(archive.namelist()) == 2  # workbook and manifest only
    workbook = sheets(get(exports).content)
    passport_row = next(row for row in workbook["Document checklist"] if row[0] == "Passport")
    assert passport_row[4] == "Marked manually (no file attached)" and passport_row[5] == ""
    assert passport_row[-3] == "Staff" and passport_row[-1] == "Checked paper copy =1+1"
    with exports.sessions() as db:
        table = sheets(candidate_search.export_candidates(db, candidate_search.Search()))
    rows = table["Candidates"]
    candidate = next(row for row in rows[1:] if row[rows[0].index("CRM reference")] == "candidate")
    assert candidate[rows[0].index("Passport document")] == "Marked manually (no file attached)"
    assert "Passport" not in candidate[rows[0].index("Missing mandatory documents")]
    assert table["Manual document confirmations"][1][-1] == "Checked paper copy =1+1"


def test_profile_and_single_query_snapshot_include_manual_status_without_versions(desk):
    assert mark(desk).status_code == 200
    desk.client.app.include_router(candidate_routes.router)
    with desk.sessions() as db:
        engine = db.get_bind()
    with recorded_statements(engine) as statements:
        response = desk.client.get("/api/candidates/candidate")
    assert response.status_code == 200, response.text
    assert len(statements) == 1
    passport = next(row for row in response.json()["documents"] if row["key"] == "passport")
    assert passport["uploaded"] and not passport["fileUploaded"] and passport["versions"] == []
    assert passport["manualStatus"]["by"]["name"] == "Staff"
    with desk.sessions() as db, recorded_statements(engine) as statements:
        snapshot = read_snapshot(db, "staff")
    assert len(statements) == 1
    assert statuses_by_candidate(snapshot["manual_documents"])["candidate"]["passport"]["uploaded"]


def test_snapshot_excludes_unrelated_audit_history_and_arbitrary_status_metadata(desk):
    with desk.sessions() as db:
        db.add_all([
            AuditEntry(actor_id="staff", action="Internal private action", entity_type="candidate", entity_id="candidate",
                       target="Private target", meta={"slot": "passport", "uploaded": True, "note": "Private unrelated note"}),
            AuditEntry(actor_id="staff", action=ACTION, entity_type="candidate", entity_id="candidate",
                       target="Private target", meta={"slot": "passport", "uploaded": True, "note": "Paper checked", "private": "Hidden arbitrary metadata"}),
        ])
        db.commit()
        snapshot = read_snapshot(db, "staff")
    text = json.dumps(snapshot["manual_documents"])
    assert "Paper checked" in text
    assert "Private" not in text and "Hidden" not in text and "meta" not in snapshot["manual_documents"][0]


def test_manual_changes_notify_document_views_only_after_commit(desk):
    with desk.sessions() as db:
        listener = Listener(db.get_bind())
    try:
        assert mark(desk).status_code == 200
        messages = listener.messages()
        assert len(messages) == 1
        assert messages[0]["t"] == ["candidates", "documents", "reports"]
        assert messages[0]["ids"] == {"candidates": ["candidate"]}
        assert "Received the paper" not in json.dumps(messages)
        with desk.sessions() as db:
            application_form.set_document_status(db, desk.actor, "candidate", "passport", False, "Cancelled change")
            db.flush()
            db.rollback()
        assert listener.messages(0.2) == []
        assert form(desk)["documents"]["passport"]["uploaded"]
    finally:
        listener.close()


def test_manual_change_invalidates_cached_views_on_commit_but_not_rollback(desk, monkeypatch):
    from app.services import response_cache as cache
    monkeypatch.setenv("CRM_RESPONSE_CACHE", "force")
    monkeypatch.setenv("CRM_LIVE_EVENTS", "0")
    cache.clear()
    assert cache.cached("manual-doc-test", ("documents",), 60, lambda: "before") == "before"
    assert mark(desk).status_code == 200
    assert cache.cached("manual-doc-test", ("documents",), 60, lambda: "after") == "after"
    with desk.sessions() as db:
        application_form.set_document_status(db, desk.actor, "candidate", "passport", False, "Cancelled")
        db.flush()
        db.rollback()
    assert cache.cached("manual-doc-test", ("documents",), 60, lambda: "WRONG") == "after"
    cache.clear()
