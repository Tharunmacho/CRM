import hashlib
import pytest
from app.core.config import Settings
from app.db.models import (Application, ApplicationStage, Attachment, Client, Company, Designation,
                           DocumentExtractionJob, MailAccount, Order,
                           MailDraft, MailDraftAttachment, MailSendAttempt, MultiPersonIntake,
                           WhatsAppChannel, WhatsAppConversation, WhatsAppSendAttempt)
from app import storage_admin
from app.services.document_storage import put_bytes, read_bytes
from test_workflow_scheduler import workspace
from test_database_naming import schema_database


def seed(db, settings):
    key = put_bytes(settings, "candidate/source.pdf", b"original")
    digest = hashlib.sha256(b"original").hexdigest()
    db.add(Attachment(id="stored",entity_type="candidate",entity_id="candidate",slot="resume",file_name="source.pdf",object_key=key,content_hash=digest))
    db.add(DocumentExtractionJob(id="job",owner_id="owner",group_id="group",kind="resume",filename="source.pdf",content_type="application/pdf",object_key=key,content_hash=digest,size_kb=1,
        state={"documents":{"resume":{"object_key":key,"content_hash":digest}}}))
    db.add(MailAccount(id="mail",address="synthetic@example.test",provider="gmail",sync_state={"storage_id":"original-volume","last_uid":7}))
    db.commit()
    return key


def test_migration_dry_run_and_verified_copy_preserve_originals(workspace,tmp_path,monkeypatch):
    settings=Settings(_env_file=None,storage_dir=str(tmp_path),storage_backend="local",s3_bucket="synthetic",s3_prefix="crm")
    remote={}
    def read(config,key): return remote[key] if key.startswith("s3://") else read_bytes(config,key)
    monkeypatch.setattr(storage_admin,"read_bytes",read)
    monkeypatch.setattr(storage_admin,"put_bytes",lambda config,key,data:remote.setdefault(key,data))
    with workspace() as db:
        original=seed(db,settings)
        dry=storage_admin.migrate_s3(db,settings)
        assert dry["dryRun"] and len(dry["copies"])==1 and not remote
        with pytest.raises(ValueError,match="Stop the API"):
            storage_admin.migrate_s3(db,settings,apply=True)
        result=storage_admin.migrate_s3(db,settings,apply=True,writers_stopped=True)
        assert not result["dryRun"]
        destination="s3://synthetic/crm/"+original
        assert db.get(Attachment,"stored").object_key==destination
        assert db.get(DocumentExtractionJob,"job").state["documents"]["resume"]["object_key"]==destination
        assert db.get(MailAccount,"mail").sync_state["last_uid"]==7
        assert db.get(MailAccount,"mail").sync_state["storage_id"].startswith("s3:")
        assert read_bytes(settings,original)==b"original" and remote[destination]==b"original"
        assert storage_admin.audit(db,settings)["ok"]


def test_failed_destination_verification_leaves_database_and_sources_intact(workspace,tmp_path,monkeypatch):
    settings=Settings(_env_file=None,storage_dir=str(tmp_path),storage_backend="local",s3_bucket="synthetic")
    monkeypatch.setattr(storage_admin,"read_bytes",lambda config,key:b"corrupt" if key.startswith("s3://") else read_bytes(config,key))
    monkeypatch.setattr(storage_admin,"put_bytes",lambda *args:None)
    with workspace() as db:
        original=seed(db,settings)
        with pytest.raises(ValueError,match="checksum verification"):
            storage_admin.migrate_s3(db,settings,apply=True,writers_stopped=True)
        db.rollback()
        assert db.get(Attachment,"stored").object_key==original
        assert db.get(MailAccount,"mail").sync_state["storage_id"]=="original-volume"
        assert read_bytes(settings,original)==b"original"


def test_source_changed_after_audit_is_not_copied_or_referenced(workspace, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local", s3_bucket="synthetic")
    reads, copies = [], []
    def changing_source(config, key):
        reads.append(key)
        return read_bytes(config, key) if len(reads) == 1 else b"changed-after-audit"
    monkeypatch.setattr(storage_admin, "read_bytes", changing_source)
    monkeypatch.setattr(storage_admin, "put_bytes", lambda *args: copies.append(args))
    with workspace() as db:
        original = seed(db, settings)
        with pytest.raises(ValueError, match="Source checksum changed"):
            storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        db.rollback()
        assert copies == []
        assert db.get(Attachment, "stored").object_key == original
        assert db.get(DocumentExtractionJob, "job").state["documents"]["resume"]["object_key"] == original
        assert read_bytes(settings, original) == b"original"


def test_draft_and_sent_snapshot_files_are_audited_and_migrated(workspace, tmp_path, monkeypatch):
    """Sent-only bytes must remain recoverable after a draft attachment is removed."""
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local",
                        s3_bucket="synthetic", s3_prefix="crm")
    remote = {}
    monkeypatch.setattr(storage_admin, "read_bytes", lambda config, key:
                        remote[key] if key.startswith("s3://") else read_bytes(config, key))
    monkeypatch.setattr(storage_admin, "put_bytes", lambda config, key, data: remote.setdefault(key, data))
    with workspace() as db:
        seed(db, settings)
        db.add(MailDraft(id="mail-draft", account_id="mail", actor_id="owner", status="sent"))
        db.flush()
        current_key = put_bytes(settings, "mail-drafts/current.pdf", b"current-file")
        sent_key = put_bytes(settings, "mail-drafts/earlier.pdf", b"sent-file")
        current_hash = hashlib.sha256(b"current-file").hexdigest()
        sent_hash = hashlib.sha256(b"sent-file").hexdigest()
        db.add(MailDraftAttachment(id="draft-file", draft_id="mail-draft", file_name="current.pdf",
            object_key=current_key, content_hash=current_hash, content_type="application/pdf", size_bytes=12))
        snapshot = {"subject": "Retained sent evidence", "to": ["recipient@example.test"],
                    "attachments": [{"object_key": sent_key, "content_hash": sent_hash, "file_name": "earlier.pdf"}]}
        db.add(MailSendAttempt(id="send-attempt", draft_id="mail-draft", actor_id="owner",
            request_key="storage-snapshot-test", status="sent", snapshot=snapshot))
        db.commit()
        report = storage_admin.audit(db, settings)
        assert report["ok"]
        assert {item["key"] for item in report["objects"]} >= {current_key, sent_key}
        storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        assert db.get(MailDraftAttachment, "draft-file").object_key == "s3://synthetic/crm/" + current_key
        saved = db.get(MailSendAttempt, "send-attempt").snapshot
        assert saved["subject"] == snapshot["subject"] and saved["to"] == snapshot["to"]
        assert saved["attachments"][0] == {**snapshot["attachments"][0], "object_key": "s3://synthetic/crm/" + sent_key}
        assert remote["s3://synthetic/crm/" + sent_key] == read_bytes(settings, sent_key) == b"sent-file"
        assert storage_admin.audit(db, settings)["ok"]


def add_application(db, *, stage=5, draft=None):
    db.add_all([Client(id="storage-client", name="Storage client"),
                Designation(id="storage-designation", title="Storage role")])
    db.flush()
    db.add(Company(id="storage-company", client_id="storage-client", name="Storage employer"))
    db.flush()
    db.add(Order(id="storage-order", ref="STORAGE-ORDER", company_id="storage-company", designation_id="storage-designation"))
    db.flush()
    row = Application(id="storage-application", candidate_id="candidate", order_id="storage-order",
                      current_stage=stage, draft=draft or {})
    db.add(row)
    db.flush()
    return row


def mock_bucket(monkeypatch):
    remote = {}
    monkeypatch.setattr(storage_admin, "read_bytes", lambda config, key:
                        remote[key] if key.startswith("s3://") else read_bytes(config, key))
    monkeypatch.setattr(storage_admin, "put_bytes", lambda config, key, data: remote.setdefault(key, data))
    return remote


def test_legacy_stage_keys_follow_verified_migration_without_rewriting_ids_urls_or_other_strings(workspace, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local", s3_bucket="synthetic", s3_prefix="crm")
    remote = mock_bucket(monkeypatch)
    with workspace() as db:
        key = seed(db, settings)
        application = add_application(db, draft={"file": key, "notes": key, "nested": {"object_key": key}})
        db.add(Attachment(id="legacy-offer", entity_type="application", entity_id=application.id,
                          slot="file", file_name="offer.pdf", object_key=key))
        db.add(Attachment(id="candidate-passport", entity_type="candidate", entity_id="candidate",
                          slot="passport", file_name="passport.pdf", object_key=key))
        modern = {"id": "legacy-offer", "url": "/api/files/legacy-offer", "name": "offer.pdf"}
        stages = [(5, 1, {"file": key, "notes": key}), (7, 1, {"copy": key, "heldBy": "owner"}),
                  (8, 1, {"receipt": key}), (9, 1, {"file": modern}),
                  (10, 1, {"file": "/api/files/legacy-offer"}), (11, 1, {"file": "legacy-offer"}),
                  (6, 1, {"file": key}), (5, 2, {"file": "other/not-owned.pdf"})]
        db.add(Attachment(id="receipt", entity_type="application", entity_id=application.id,
                          slot="stage_8_receipt", file_name="receipt.pdf", object_key=key))
        foreign_key = put_bytes(settings, "other/not-owned.pdf", b"other-owner")
        db.add(Attachment(id="foreign", entity_type="application", entity_id="another-application",
                          slot="stage_5_file", file_name="other.pdf", object_key=foreign_key))
        for stage, attempt, data in stages:
            db.add(ApplicationStage(id=f"stage-{stage}-{attempt}", application_id=application.id,
                                    stage=stage, attempt=attempt, data=data))
        db.commit()
        preview = storage_admin.migrate_s3(db, settings)
        assert preview["legacyApplicationReferences"] == 4
        assert application.draft["file"] == key and not remote
        storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        destination = "s3://synthetic/crm/" + key
        normalized = {"id": "legacy-offer", "url": "/api/files/legacy-offer", "name": "offer.pdf", "sizeKb": None, "version": 1}
        assert application.draft == {"file": normalized, "notes": key, "nested": {"object_key": key}}
        assert db.get(ApplicationStage, "stage-5-1").data == {"file": normalized, "notes": key}
        assert db.get(ApplicationStage, "stage-7-1").data["copy"]["id"] == "candidate-passport"
        assert db.get(ApplicationStage, "stage-8-1").data["receipt"]["id"] == "receipt"
        for stage, attempt, data in stages[3:]:
            assert db.get(ApplicationStage, f"stage-{stage}-{attempt}").data == data
        assert db.get(Attachment, "legacy-offer").object_key == destination
        # The migrated legacy generic-slot reference still resolves in the stage API.
        from app.api.routers.applications import _normalized_data
        assert _normalized_data(db, application, {"file": application.draft["file"]})["file"]["id"] == "legacy-offer"
        assert storage_admin.audit(db, settings)["ok"]


def test_ambiguous_owned_legacy_reference_blocks_migration_before_copy(workspace, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local", s3_bucket="synthetic")
    remote = mock_bucket(monkeypatch)
    with workspace() as db:
        key = seed(db, settings)
        application = add_application(db, draft={"file": key})
        for identifier, slot in (("old", "file"), ("new", "stage_5_file")):
            db.add(Attachment(id=identifier, entity_type="application", entity_id=application.id,
                              slot=slot, file_name="offer.pdf", object_key=key))
        db.commit()
        with pytest.raises(ValueError, match="Ambiguous legacy application"):
            storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        db.rollback()
        assert not remote
        assert db.get(Attachment, "stored").object_key == key
        assert application.draft["file"] == key


def test_missing_manifest_retains_exact_missing_references_and_reports_incomplete(workspace, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local", s3_bucket="synthetic", s3_prefix="crm")
    remote = mock_bucket(monkeypatch)
    with workspace() as db:
        available = seed(db, settings)
        missing = "old/missing.pdf"
        digest = hashlib.sha256(b"known-missing-original").hexdigest()
        application = add_application(db, draft={"file": missing})
        db.add(Attachment(id="missing-file", entity_type="application", entity_id=application.id,
                          slot="stage_5_file", file_name="missing.pdf", object_key=missing, content_hash=digest))
        job = db.get(DocumentExtractionJob, "job")
        job.state = {**job.state, "previous": {"object_key": missing, "content_hash": digest}}
        db.commit()
        with pytest.raises(ValueError, match="Source audit failed"):
            storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        db.rollback()
        assert not remote
        manifest = {"version": 1, "objects": [{"key": missing, "expectedHashes": [digest]}]}
        preview = storage_admin.migrate_s3(db, settings, missing_manifest=manifest)
        assert preview["complete"] is False and [item["key"] for item in preview["retainedMissingObjects"]] == [missing]
        assert [item["from"] for item in preview["copies"]] == [available]
        assert db.get(Attachment, "stored").object_key == available and not remote
        result = storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True, missing_manifest=manifest)
        assert result["complete"] is False
        assert db.get(Attachment, "stored").object_key.startswith("s3://")
        assert db.get(Attachment, "missing-file").object_key == missing
        assert job.state["previous"]["object_key"] == missing
        assert application.draft["file"] == missing
        report = storage_admin.audit(db, settings)
        assert report["ok"] is False
        failure = next(item for item in report["objects"] if item["key"] == missing)
        assert failure["missing"] and failure["expectedHashes"] == [digest]
        assert read_bytes(settings, available) == b"original"


@pytest.mark.parametrize("change", ["unknown", "hash", "recovered", "corrupt", "permission", "remote", "duplicate"])
def test_missing_manifest_cannot_hide_stale_entries_or_other_storage_failures(change):
    digest = "a" * 64
    item = {"key": "missing.pdf", "status": "unavailable", "missing": True, "expectedHashes": [digest]}
    manifest = {"version": 1, "objects": [{"key": item["key"], "expectedHashes": [digest]}]}
    if change == "unknown":
        manifest["objects"][0]["key"] = "unreviewed.pdf"
    elif change == "hash":
        manifest["objects"][0]["expectedHashes"] = ["b" * 64]
    elif change == "recovered":
        item["status"] = "ok"
    elif change == "corrupt":
        item["status"] = "checksum_mismatch"
    elif change == "permission":
        item["missing"] = False
    elif change == "remote":
        item["key"] = manifest["objects"][0]["key"] = "s3://other/missing.pdf"
    elif change == "duplicate":
        manifest["objects"].append(dict(manifest["objects"][0]))
    with pytest.raises(ValueError, match="manifest"):
        storage_admin.approved_missing({"objects": [item]}, manifest)


def test_storage_migration_preserves_all_worker_bindings_and_reports_transition(workspace, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local", s3_bucket="synthetic", instance_name=None)
    mock_bucket(monkeypatch)
    with workspace() as db:
        key = seed(db, settings)
        db.get(DocumentExtractionJob, "job").instance = "explicit-local-owner"
        db.add(DocumentExtractionJob(id="other-job", owner_id="other", group_id="other-group", kind="resume",
                                    filename="source.pdf", content_type="application/pdf", object_key=key,
                                    content_hash=hashlib.sha256(b"original").hexdigest(), size_kb=1,
                                    instance="hosted-owner", status="retry"))
        db.commit()
        result = storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        assert db.get(DocumentExtractionJob, "job").instance == "explicit-local-owner"
        assert db.get(DocumentExtractionJob, "other-job").instance == "hosted-owner"
        bindings = result["workerInstances"]
        assert bindings["bindingsPreserved"] is True
        assert bindings["sourceInstance"] != bindings["targetInstance"]
        assert bindings["queuedJobs"] == [{"instance": "explicit-local-owner", "count": 1}, {"instance": "hosted-owner", "count": 1}]
        fixed = settings.model_copy(update={"instance_name": "explicit-local-owner"})
        followup = storage_admin.migrate_s3(db, fixed)
        assert followup["workerInstances"]["sourceInstance"] == followup["workerInstances"]["targetInstance"] == "explicit-local-owner"


def test_whatsapp_outbox_and_split_source_bytes_survive_verified_storage_migration(workspace, tmp_path, monkeypatch):
    settings = Settings(_env_file=None, storage_dir=str(tmp_path), storage_backend="local", s3_bucket="synthetic", s3_prefix="crm")
    remote = {}
    monkeypatch.setattr(storage_admin, "read_bytes", lambda config, key: remote[key] if key.startswith("s3://") else read_bytes(config, key))
    monkeypatch.setattr(storage_admin, "put_bytes", lambda config, key, data: remote.setdefault(key, data))
    with workspace() as db:
        # The reviewed source stays recoverable even when no candidate has yet
        # been committed. Derived person PDFs use ordinary Attachment rows.
        source = put_bytes(settings, "split/original.pdf", b"two-person-source")
        outgoing = put_bytes(settings, "whatsapp/outbound/file.pdf", b"outgoing-message")
        derived = put_bytes(settings, "split/person-resume.pdf", b"selected-person-pages")
        digest = lambda value: hashlib.sha256(value).hexdigest()
        db.add(WhatsAppChannel(phone_number_id="12345")); db.flush()
        db.add(WhatsAppConversation(id="chat", phone_number_id="12345", peer_number="919999999999")); db.flush()
        db.add(WhatsAppSendAttempt(id="outgoing", conversation_id="chat", sender_user_id="owner", request_key="storage-chat-test",
            payload_hash="a" * 64, message_type="document", state="unknown", object_key=outgoing, content_hash=digest(b"outgoing-message")))
        db.add(MultiPersonIntake(id="bundle", source_type="manual", source_id="original-job", created_by="owner",
            sources=[{"id": "original-job", "object_key": source, "hash": digest(b"two-person-source"), "name": "original.pdf"}]))
        db.add(Attachment(id="derived", entity_type="candidate", entity_id="candidate", slot="resume", file_name="person-resume.pdf",
            object_key=derived, content_hash=digest(b"selected-person-pages")))
        db.commit()
        report = storage_admin.audit(db, settings)
        assert report["ok"] and {row["key"] for row in report["objects"]} == {source, outgoing, derived}
        storage_admin.migrate_s3(db, settings, apply=True, writers_stopped=True)
        assert db.get(WhatsAppSendAttempt, "outgoing").object_key == "s3://synthetic/crm/" + outgoing
        assert db.get(WhatsAppSendAttempt, "outgoing").state == "unknown"
        assert db.get(MultiPersonIntake, "bundle").sources[0]["object_key"] == "s3://synthetic/crm/" + source
        assert db.get(Attachment, "derived").object_key == "s3://synthetic/crm/" + derived
        assert all(read_bytes(settings, key) for key in (source, outgoing, derived))
        assert storage_admin.audit(db, settings)["ok"]
