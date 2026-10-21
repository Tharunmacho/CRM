import hashlib
import pytest
from app.core.config import Settings
from app.db.models import (Attachment, DocumentExtractionJob, MailAccount,
                           MailDraft, MailDraftAttachment, MailSendAttempt)
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
