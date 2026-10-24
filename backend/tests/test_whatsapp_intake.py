"""Verified WhatsApp receipts and durable extraction with synthetic provider calls."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import hmac
import json
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import Attachment, AuditEntry, Candidate, IntakeAcknowledgement, Task, User, WhatsAppChannel, WhatsAppMessage
from app.integrations.whatsapp import download_media, verify_signature, WhatsAppError
from app.services.document_extraction import ExtractionFailure, ExtractionPending
from app.services.whatsapp_intake import process_message, receive_payload
from app.services.document_storage import _local
from test_workflow_scheduler import workspace
from test_database_naming import schema_database
from test_email_intake import extraction, ORIGINAL, pdf as make_pdf


@pytest.fixture
def setup(workspace, tmp_path, monkeypatch):
    # Keep synthetic checkpoint tests independent of installed local OCR;
    # native person inspection remains enabled for the stored PDF.
    from app.integrations.ocr import inspection
    monkeypatch.setattr(inspection, "find_tesseract", lambda _command=None: None)
    settings = SimpleNamespace(whatsapp_enabled=True, whatsapp_phone_number_id="12345", whatsapp_graph_version="v24.0",
        whatsapp_access_token="synthetic-access-token", whatsapp_app_secret="synthetic-app-secret", whatsapp_verify_token="synthetic-verify-token",
        storage_dir=str(tmp_path), storage_backend="local", ocr_document_timeout_seconds=600)
    with workspace() as db:
        db.get(User, "owner").receives_auto_assign = True
        db.add(WhatsAppChannel(phone_number_id=settings.whatsapp_phone_number_id, enabled=True))
        db.commit()
    return workspace, settings


def payload(message_id="wamid.synthetic", *, phone="12345", kind="document"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": phone, "display_phone_number": "+91 00000 00000"},
        "contacts": [{"wa_id": "919999999999", "profile": {"name": "Sender is not document identity"}}],
        "messages": [{"id": message_id, "from": "919999999999", "timestamp": "1792570000", "type": kind,
                      "document": {"id": "777", "filename": "resume.pdf", "mime_type": "application/pdf"}}],
    }}]}]}


def receipt(sessions, settings):
    with sessions() as db:
        assert receive_payload(db, payload(), settings) == 1
        return db.scalar(select(WhatsAppMessage.id))


def test_signature_verification_never_accepts_missing_or_tampered_body():
    body, secret = b'{"object":"whatsapp_business_account"}', "test-secret"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, signature, secret)
    assert not verify_signature(body + b" ", signature, secret)
    assert not verify_signature(body, None, secret)
    assert not verify_signature(body, "sha256=é", secret)


def test_webhook_handshake_authentication_and_repeat_safe_receipt(setup, monkeypatch):
    from app.api.routers import whatsapp
    sessions, settings = setup
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    app = FastAPI(); app.include_router(whatsapp.router)
    def db():
        with sessions() as session: yield session
    app.dependency_overrides[whatsapp.get_db] = db
    with TestClient(app) as client:
        params = {"hub.mode": "subscribe", "hub.verify_token": settings.whatsapp_verify_token, "hub.challenge": "challenge123"}
        assert client.get("/api/whatsapp/webhook", params=params).text == "challenge123"
        params["hub.verify_token"] = "incorrect"
        assert client.get("/api/whatsapp/webhook", params=params).status_code == 403
        params["hub.verify_token"] = "\u00e9"
        assert client.get("/api/whatsapp/webhook", params=params).status_code == 403
        raw = json.dumps(payload()).encode()
        assert client.post("/api/whatsapp/webhook", content=raw).status_code == 401
        signature = "sha256=" + hmac.new(settings.whatsapp_app_secret.encode(), raw, hashlib.sha256).hexdigest()
        headers = {"X-Hub-Signature-256": signature, "Content-Type": "application/json"}
        assert client.post("/api/whatsapp/webhook", content=raw, headers=headers).json() == {"received": 1}
        assert client.post("/api/whatsapp/webhook", content=raw, headers=headers).json() == {"received": 0}


def test_wrong_phone_account_does_not_receive_messages(setup):
    sessions, settings = setup
    with sessions() as db:
        assert receive_payload(db, payload(phone="different-business-phone"), settings) == 0
        assert db.scalar(select(func.count()).select_from(WhatsAppMessage)) == 0


def test_original_and_selected_documents_candidate_owner_and_replay(setup):
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    output = extraction(fields={"name": "Document Identity", "phone": "9000000002", "email": "document@example.test"})
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=lambda *_, **__: deepcopy(output))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        candidate = db.get(Candidate, row.candidate_id)
        assert candidate.name == "Document Identity"
        assert candidate.phone == "9000000002"  # Sender is separate provenance.
        assert candidate.whatsapp == "919999999999"
        assert candidate.owner_id == candidate.intake_owner_id == "owner"
        assert candidate.source.value == "whatsapp"
        assert set(db.scalars(select(Attachment.slot).where(Attachment.entity_id == candidate.id))) == {"source_bundle", "resume"}
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate.id)) == 1
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: pytest.fail("Completed receipt must not fetch media again"))


def test_pending_provider_job_resumes_without_refetch_or_duplicate_assignment(setup):
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    def pending(data, state, save, **kwargs):
        state["provider_job"] = "existing-job"
        save(state)
        raise ExtractionPending("existing-job", "resume")
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=pending)
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "retry" and row.attempts == 0
    def resumed(data, state, **kwargs):
        assert state["provider_job"] == "existing-job"
        return extraction(fields={"phone": "9000000002"})
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: pytest.fail("Original should be durable"), extract=resumed)
    with sessions() as db:
        assert db.get(WhatsAppMessage, message_id).status == "completed"


def test_existing_candidate_identity_is_enriched_without_reassignment(setup):
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    with sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name, candidate.skills, candidate.total_experience = "Human correction", ["Human skill"], 0
        db.commit()
    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
        extract=lambda *_, **__: extraction(fields={"phone": "9000000001", "skills": ["OCR skill"], "total_experience": 5}))
    with sessions() as db:
        row, candidate = db.get(WhatsAppMessage, message_id), db.get(Candidate, "candidate")
        assert row.status == "completed" and row.candidate_id == candidate.id
        assert candidate.name == "Human correction" and candidate.skills == ["Human skill"] and candidate.total_experience == 0
        assert db.scalar(select(func.count()).select_from(Task)) == 0


def test_whatsapp_keeps_verbatim_provider_output_beside_provenance_and_adds_a_second_passport_to_an_old_shape_candidate(setup):
    sessions, settings = setup
    old = {"request_id": "passport-old", "pages": [{"page_number": 3, "text": "old passport text"}], "source_sha256": "old"}
    with sessions() as db:
        db.get(Candidate, "candidate").ocr_data = {"passport": old}
        db.commit()
    message_id = receipt(sessions, settings)
    output = extraction(kind="passport", fields={"phone": "9000000001"})
    output["extractions"]["passport"]["raw"] = {"request_id": "passport-new", "mrz_source": "mrz", "raw_mrz": "P<IND",
        "pages": [{"page_number": 1, "text": "new passport text", "lines": [{"text": "kept"}]}], "unmodelled": {"kept": 1}}
    output["extractions"]["passport"]["selected_pages"] = [3]
    output["extractions"]["passport"]["source"] = {"local_inspection": {"selected_pages": [3]}, "selected_pages": [3], "source_sha256": "new"}

    process_message(message_id, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
        extract=lambda *_, **__: deepcopy(output))

    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "completed", row.last_error
        stored = db.get(Candidate, row.candidate_id).ocr_data
        assert stored["passport"] == old
        (entry,) = stored["additional_documents"]
        assert entry["passport"] == output["extractions"]["passport"]["raw"]
        assert entry["_source"] == {
            "source_file": {"channel": "whatsapp", "message_id": message_id, "file_name": "resume.pdf", "selected_pages": [3]},
            "local_inspection": {"selected_pages": [3]}, "selected_pages": [3], "source_sha256": "new"}
        assert "_sources" not in stored


@pytest.mark.parametrize("provider_status", ["failed", "queued", "running", "submitting", "unknown", "succeeded"])
def test_automatic_retry_restarts_only_confirmed_failed_provider_jobs(setup, provider_status):
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    provider_record = {"job_id": "accepted-job", "status": provider_status,
                       "idempotency_key": "saved-submission-key", "selected_pages": [1]}
    if provider_status == "succeeded":
        provider_record["coverage_status"] = "needs_review"

    def interrupted(data, *, state, save, **kwargs):
        state["source_sha256"] = hashlib.sha256(data).hexdigest()
        state["provider_jobs"] = {"resume": deepcopy(provider_record)}
        save(state)
        raise ExtractionFailure("Synthetic provider processing failure")

    process_message(message_id, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=interrupted)
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        state = row.processing_state["extraction"]
        assert row.status == "retry" and row.attempts == 1
        assert row.processing_state["originalHash"] == hashlib.sha256(ORIGINAL).hexdigest()
        if provider_status == "failed":
            assert state["provider_jobs"] == {}
            assert state["provider_generations"] == {"resume": 1}
            assert state["provider_job_history"]["resume"] == [provider_record]
        else:
            assert state["provider_jobs"]["resume"] == provider_record
            assert not state.get("provider_generations")


def test_confirmed_provider_failure_gets_three_retries_then_preserves_exhausted_job(setup):
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    submissions = []

    def failed_job(data, *, state, save, **kwargs):
        jobs = state.setdefault("provider_jobs", {})
        if "resume" not in jobs:
            generation = state.get("provider_generations", {}).get("resume", 0)
            submissions.append(generation)
            jobs["resume"] = {"job_id": f"failed-job-{generation}", "status": "failed"}
        save(state)
        raise ExtractionFailure("The accepted resume job failed")

    for attempt in range(1, 5):
        process_message(message_id, sessions=sessions, settings=settings,
                        fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=failed_job)
        with sessions() as db:
            row = db.get(WhatsAppMessage, message_id)
            assert row.attempts == attempt
            assert row.status == ("retry" if attempt < 4 else "failed")
            assert row.candidate_id is None
            assert db.scalar(select(func.count()).select_from(Task)) == 0

    assert submissions == [0, 1, 2, 3]
    with sessions() as db:
        state = db.get(WhatsAppMessage, message_id).processing_state["extraction"]
        assert state["provider_jobs"]["resume"]["job_id"] == "failed-job-3"
        assert len(state["provider_job_history"]["resume"]) == 3
    process_message(message_id, sessions=sessions, settings=settings,
                    extract=lambda *_, **__: pytest.fail("Exhausted work requires an explicit retry"))


def test_media_fetch_keeps_bearer_token_on_meta_hosts_and_checks_hash():
    settings = SimpleNamespace(whatsapp_access_token="test-token", whatsapp_graph_version="v24.0", whatsapp_phone_number_id="12345")
    data = b"synthetic document"
    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.host == "graph.facebook.com":
            return httpx.Response(200, json={"url": "https://lookaside.fbsbx.com/media", "mime_type": "application/pdf", "sha256": hashlib.sha256(data).hexdigest()})
        return httpx.Response(200, content=data)
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert download_media(settings, "777", client=client) == (data, "application/pdf")
    for address in ("https://untrusted.example/media", "https://lookaside.fbsbx.com:8443/media", "https://user:password@lookaside.fbsbx.com/media"):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"url": address}))) as client:
            with pytest.raises(WhatsAppError, match="unsupported media address"):
                download_media(settings, "777", client=client)


def test_review_cannot_complete_without_retained_original(setup):
    from app.api.routers.whatsapp import resolve, ResolveIn
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        row.status = "needs_review"
        db.commit()
        with pytest.raises(HTTPException) as rejected:
            resolve(message_id, ResolveIn(candidateId="candidate", reason="Reviewed existing candidate"), db, db.get(User, "admin"))
        assert rejected.value.status_code == 422
        assert row.status == "needs_review" and row.candidate_id is None
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0


def test_cached_selected_document_corruption_blocks_candidate_commit(setup, monkeypatch):
    from app.services import whatsapp_intake
    sessions, settings = setup
    message_id = receipt(sessions, settings)
    def interrupted_commit(*_):
        raise RuntimeError("Synthetic interruption after durable extraction")
    with monkeypatch.context() as patch:
        patch.setattr(whatsapp_intake, "commit_candidate", interrupted_commit)
        process_message(message_id, sessions=sessions, settings=settings,
                        fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                        extract=lambda *_, **__: extraction(fields={"phone": "9000000002"}))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "retry"
        document = next(iter(row.processing_state["documents"].values()))
        _local(settings, document["object_key"]).write_bytes(b"altered selected document")
    process_message(message_id, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: pytest.fail("Must reuse retained original"),
                    extract=lambda *_, **__: pytest.fail("Must reuse extraction checkpoint"))
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "needs_review" and row.candidate_id is None
        assert "checksum" in row.last_error
        assert db.scalar(select(func.count()).select_from(Candidate)) == 1
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0


def test_duplicate_content_reuses_validated_extraction_and_keeps_new_receipt_on_admin_retry(setup):
    sessions, settings = setup
    with sessions() as db:
        db.get(Candidate, "candidate").phone = "919999999999"
        db.commit()
    first = receipt(sessions, settings)
    process_message(first, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: extraction(fields={"phone": "9000000002"}))
    with sessions() as db:
        original = db.get(WhatsAppMessage, first)
        candidate_id = original.candidate_id
        assert candidate_id != "candidate"  # A sender contact is never identity evidence.
        candidate = db.get(Candidate, candidate_id)
        candidate.owner_id, candidate.name, candidate.whatsapp = "other", "Human name", "971555555555"
        receive_payload(db, payload("wamid.second-receipt"), settings)
        second = db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == "wamid.second-receipt"))
    process_message(second, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: pytest.fail("Identical bytes must not repeat paid extraction"))
    with sessions() as db:
        # PRD: a file whose hash was already processed is skipped: receipt kept, nothing created.
        row = db.get(WhatsAppMessage, second)
        assert row.status == "ignored" and "Duplicate file" in row.last_error
        assert row.candidate_id is None and row.content_hash == db.get(WhatsAppMessage, first).content_hash
        audit = db.scalar(select(AuditEntry).where(AuditEntry.entity_id == second, AuditEntry.action == "Ignored duplicate WhatsApp file"))
        assert audit.meta["providerMessageId"] == "wamid.second-receipt" and audit.meta["matched"]["candidateId"] == candidate_id
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate_id)) == 1
        # An admin retry overrides the skip and reuses the validated extraction without a paid rescan.
        row.status = "pending"
        db.commit()
    process_message(second, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: pytest.fail("The stored original is reused"),
                    extract=lambda *_, **__: pytest.fail("Identical bytes must not repeat paid extraction"))
    with sessions() as db:
        row = db.get(WhatsAppMessage, second)
        assert row.status == "completed" and row.candidate_id == candidate_id
        assert row.processing_state["reusedReceiptId"] == first
        assert row.object_key != db.get(WhatsAppMessage, first).object_key
        candidate = db.get(Candidate, candidate_id)
        assert (candidate.owner_id, candidate.intake_owner_id, candidate.name, candidate.whatsapp) == ("other", "owner", "Human name", "971555555555")
        assert db.scalar(select(func.count()).select_from(Task).where(Task.candidate_id == candidate_id)) == 1


def test_duplicate_file_is_skipped_without_candidate_ack_or_ocr_but_new_version_is_attached(setup):
    sessions, settings = setup
    first = receipt(sessions, settings)
    process_message(first, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: extraction(fields={"phone": "9000000002"}))
    with sessions() as db:
        candidate_id = db.get(WhatsAppMessage, first).candidate_id
        resume_versions = db.scalar(select(func.count()).select_from(Attachment).where(Attachment.entity_id == candidate_id, Attachment.slot == "source_bundle"))
        receive_payload(db, payload("wamid.same-bytes"), settings)
        receive_payload(db, payload("wamid.new-version"), settings)
        same = db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == "wamid.same-bytes"))
        newer = db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == "wamid.new-version"))
    process_message(same, sessions=sessions, settings=settings, fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: pytest.fail("A duplicate file must not be read"))
    # Different bytes (a revised resume) are processed, matched to the same person and attached as the latest version.
    process_message(newer, sessions=sessions, settings=settings, fetch_media=lambda *_: (make_pdf("Page one: certificate", "Page two: resume revised", "Page three: passport"), "application/pdf"),
                    extract=lambda *_, **__: extraction(fields={"phone": "9000000002"}))
    with sessions() as db:
        assert db.get(WhatsAppMessage, same).status == "ignored"
        assert db.scalar(select(func.count()).select_from(Candidate)) == 2  # seeded candidate + one from the first file
        assert db.get(WhatsAppMessage, newer).status == "completed" and db.get(WhatsAppMessage, newer).candidate_id == candidate_id
        versions = list(db.scalars(select(Attachment.version).where(Attachment.entity_id == candidate_id, Attachment.slot == "source_bundle").order_by(Attachment.version)))
        assert versions == list(range(1, resume_versions + 2))
        # The ignored duplicate queued nothing of its own.
        assert db.scalar(select(func.count()).select_from(IntakeAcknowledgement).where(IntakeAcknowledgement.source_id == same)) == 0


def test_changed_cached_original_does_not_reuse_extraction(setup):
    sessions, settings = setup
    first = receipt(sessions, settings)
    process_message(first, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                    extract=lambda *_, **__: extraction(fields={"phone": "9000000002"}))
    with sessions() as db:
        _local(settings, db.get(WhatsAppMessage, first).object_key).write_bytes(b"changed cached original")
        receive_payload(db, payload("wamid.valid-new-original"), settings)
        second = db.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == "wamid.valid-new-original"))
    calls = []
    def fresh_extract(*_, **__):
        calls.append(True)
        return extraction(fields={"phone": "9000000002"})
    process_message(second, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=fresh_extract)
    with sessions() as db:
        # Identical bytes are skipped first; the admin retry then decides whether the cached read is trustworthy.
        assert db.get(WhatsAppMessage, second).status == "ignored" and calls == []
        db.get(WhatsAppMessage, second).status = "pending"
        db.commit()
    process_message(second, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=fresh_extract)
    with sessions() as db:
        row = db.get(WhatsAppMessage, second)
        assert row.status == "completed"
        assert "reusedReceiptId" not in row.processing_state
        assert calls == [True]


def test_whatsapp_health_is_admin_only_and_omits_message_content(setup, monkeypatch):
    from app.api.routers import whatsapp
    sessions, settings = setup
    receipt(sessions, settings)
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    app = FastAPI(); app.include_router(whatsapp.router)
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[whatsapp.get_db] = database
    with sessions() as db:
        app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, "owner")
        with TestClient(app) as client:
            assert client.get("/api/whatsapp/health").status_code == 403
            app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, "admin")
            response = client.get("/api/whatsapp/health")
            assert response.status_code == 200
            result = response.json()
            assert result["configured"] and result["counts"]["pending"] == 1
            assert result["channels"][0]["id"] == "12345"
            assert "messages" not in result and "919999999999" not in response.text


def test_admin_connect_disconnect_stops_receipts_and_queued_work_but_retains_files(setup, monkeypatch):
    from app.api.routers import whatsapp
    sessions, settings = setup
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    with sessions() as db:
        db.delete(db.get(WhatsAppChannel, "12345")); db.commit()
        assert receive_payload(db, payload(), settings) == 0
        assert db.get(WhatsAppChannel, "12345") is None
    app = FastAPI(); app.include_router(whatsapp.router)
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[whatsapp.get_db] = database
    with sessions() as db, TestClient(app) as client:
        for role in ("owner", "manager"):
            app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, role)
            assert client.post("/api/whatsapp/channels/connect").status_code == 403
            assert client.post("/api/whatsapp/channels/12345/disconnect").status_code == 403
        app.dependency_overrides[whatsapp.current_user] = lambda: db.get(User, "admin")
        assert client.post("/api/whatsapp/channels/connect").json() == {"id": "12345", "enabled": True}
        first = receipt(sessions, settings)
        process_message(first, sessions=sessions, settings=settings,
                        fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                        extract=lambda *_, **__: extraction(fields={"phone": "9000000002"}))
        with sessions() as session:
            assert receive_payload(session, payload("wamid.queued"), settings) == 1
            queued = session.scalar(select(WhatsAppMessage.id).where(WhatsAppMessage.provider_message_id == "wamid.queued"))
        assert client.post("/api/whatsapp/channels/12345/disconnect").json()["enabled"] is False
        with sessions() as session:
            assert receive_payload(session, payload("wamid.disconnected"), settings) == 0
            assert session.scalar(select(func.count()).select_from(WhatsAppMessage)) == 2
        process_message(queued, sessions=sessions, settings=settings,
                        fetch_media=lambda *_: pytest.fail("Disconnected work must not contact the provider"))
        with sessions() as session:
            assert session.get(WhatsAppMessage, queued).status == "pending"
        assert client.get(f"/api/whatsapp/messages/{first}/file").status_code == 200
        assert client.post(f"/api/whatsapp/messages/{queued}/retry").status_code == 409
        assert client.post("/api/whatsapp/channels/connect").status_code == 200
        process_message(queued, sessions=sessions, settings=settings,
                        fetch_media=lambda *_: (ORIGINAL, "application/pdf"),
                        extract=lambda *_, **__: pytest.fail("Retained identical extraction should be reused"))
        with sessions() as session:
            # Identical bytes to the first receipt: skipped as an already-processed file after reconnecting.
            assert session.get(WhatsAppMessage, queued).status == "ignored"
            assert session.scalar(select(func.count()).select_from(Task)) == 1


def test_disconnect_during_extraction_keeps_checkpoint_and_resumes_after_reconnect(setup, monkeypatch):
    from app.api.routers import whatsapp
    sessions, settings = setup
    monkeypatch.setattr(whatsapp, "get_settings", lambda: settings)
    message_id = receipt(sessions, settings)
    def paused_extract(data, state, save, **kwargs):
        state["provider_job"] = "retained-provider-job"
        with sessions() as db:
            whatsapp.disconnect_channel("12345", db, db.get(User, "admin"))
        save(state)
        pytest.fail("Processing must stop at the checkpoint after disconnect")
    process_message(message_id, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: (ORIGINAL, "application/pdf"), extract=paused_extract)
    with sessions() as db:
        row = db.get(WhatsAppMessage, message_id)
        assert row.status == "retry" and row.attempts == 0 and row.candidate_id is None
        assert row.processing_state["extraction"]["provider_job"] == "retained-provider-job"
        whatsapp.connect_channel(db, db.get(User, "admin"))
    def resumed(data, state, **kwargs):
        assert state["provider_job"] == "retained-provider-job"
        return extraction(fields={"phone": "9000000002"})
    process_message(message_id, sessions=sessions, settings=settings,
                    fetch_media=lambda *_: pytest.fail("The original must survive disconnect"), extract=resumed)
    with sessions() as db:
        assert db.get(WhatsAppMessage, message_id).status == "completed"
