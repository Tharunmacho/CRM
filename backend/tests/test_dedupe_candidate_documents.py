"""scripts/dedupe_candidate_documents.py: unlink the repeated versions an earlier import left in a slot (disposable PostgreSQL)."""
import hashlib
import importlib.util
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import people_fixtures as pf
from app.db import models as m
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dedupe_candidate_documents.py"
spec = importlib.util.spec_from_file_location("dedupe_candidate_documents", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


def pdf(person, *tokens):
    return pf.build_pdf([pf.page(person, token) for token in tokens])


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None, ocr_languages="eng")
    monkeypatch.setattr(script, "storage_settings", lambda: settings)
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id="admin", name="Admin", email="admin@example.test", role=m.Role.admin, active=True),
                    m.User(id="staff", name="Staff", email="staff@example.test", role=m.Role.staff, active=True)])
        db.flush()
        db.add(m.Candidate(id="c1", ref="CND-00001", care_id="CI-167", name="Rashid Khan", phone="9000000001", source=m.Source.walk_in, owner_id="staff"))
        db.flush()
        yield SimpleNamespace(db=db, storage=tmp_path, engine=schema_database)


def file_version(env, slot, version, name, data, *, meta=None, human=False):
    key = f"{uuid.uuid4().hex[:2]}/{uuid.uuid4().hex}-{name}"
    target = env.storage / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    row = m.Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id="c1", slot=slot, version=version, file_name=name,
                       size_kb=1, content_type="application/pdf", object_key=key, content_hash=hashlib.sha256(data).hexdigest(), uploaded_by="staff")
    env.db.add(row)
    env.db.flush()
    if not human:
        env.db.add(m.AuditEntry(actor_id="staff", action="Uploaded document", target=name, entity_type="candidate", entity_id="c1",
                                meta={"attachment_id": row.id, "slot": slot, "version": version, "bulk_import": True, **(meta or {})}))
    env.db.flush()
    return row, target


def args(**values):
    return SimpleNamespace(**{"candidate": "CI-167", "all": False, "apply": False, **values})


def seed_ci167(env):
    standalone, standalone_file = file_version(env, "passport", 1, "Rashid_Khan-Passport.pdf", pdf("tharun", "passport_data", "passport_back"))
    cut, cut_file = file_version(env, "passport", 2, "Rashid_Khan-Passport-2.pdf", pdf("tharun", "passport_data", "passport_back"),
                                 meta={"original_name": "picker.pdf", "split_pages": [2, 3]})
    merged, merged_file = file_version(env, "passport", 3, "Rashid_Khan-Passport-3.pdf", pdf("tharun", "passport_back"), meta={"original_name": "merged 10.pdf"})
    other, _ = file_version(env, "passport", 4, "Rashid_Khan-Passport-4.pdf", pdf("meera", "passport_data"), meta={"original_name": "someone else.pdf"})
    typed, _ = file_version(env, "passport", 5, "Passport by hand.pdf", pdf("tharun", "passport_data"), human=True)
    resume, _ = file_version(env, "resume", 1, "Rashid_Khan-Resume.pdf", pdf("tharun", "resume_1", "resume_2"))
    env.db.commit()
    return SimpleNamespace(standalone=standalone, cut=cut, merged=merged, other=other, typed=typed, resume=resume, files=[standalone_file, cut_file, merged_file])


def versions(env, slot):
    with Session(env.engine) as db:
        return sorted(row.version for row in db.scalars(select(m.Attachment).where(m.Attachment.entity_id == "c1", m.Attachment.slot == slot)))


def test_dry_run_shows_what_would_be_unlinked_and_changes_nothing(env, capsys):
    seed_ci167(env)
    assert script.run(env.engine, args()) == 0
    shown = capsys.readouterr().out
    assert "would unlink v2 Rashid_Khan-Passport-2.pdf" in shown and "would unlink v3 Rashid_Khan-Passport-3.pdf" in shown
    assert "unlink v4" not in shown and "unlink v5" not in shown and "Resume" not in shown and "DRY RUN: 2 duplicate version(s)" in shown
    assert versions(env, "passport") == [1, 2, 3, 4, 5]


def test_apply_unlinks_only_the_repeated_versions_keeps_every_stored_file_and_leaves_an_audit_trail(env, capsys):
    seeded = seed_ci167(env)
    assert script.run(env.engine, args(apply=True)) == 0
    assert versions(env, "passport") == [1, 4, 5] and versions(env, "resume") == [1]       # the standalone, the other person's page, the hand-uploaded one
    assert all(path.is_file() for path in seeded.files)                                      # no stored file was deleted
    with Session(env.engine) as db:
        entries = list(db.scalars(select(m.AuditEntry).where(m.AuditEntry.action == "Unlinked duplicate document")))
    assert sorted(entry.meta["version"] for entry in entries) == [2, 3]
    cut = next(entry.meta for entry in entries if entry.meta["version"] == 2)
    assert cut["attachment_id"] == seeded.cut.id and cut["file_kept_in_storage"] is True and cut["same_as_attachment_id"] == seeded.standalone.id
    assert cut["object_key"] == seeded.cut.object_key and cut["original_name"] == "picker.pdf" and cut["split_pages"] == [2, 3]
    capsys.readouterr()
    assert script.run(env.engine, args(apply=True)) == 0           # a second run finds nothing more
    assert "0 duplicate version(s)" in capsys.readouterr().out and versions(env, "passport") == [1, 4, 5]


def test_a_version_a_reading_job_points_at_is_kept(env):
    seeded = seed_ci167(env)
    env.db.add(m.DocumentExtractionJob(id=str(uuid.uuid4()), owner_id="staff", group_id="g", kind="passport", filename="x.pdf", content_type="application/pdf",
                                       object_key=seeded.cut.object_key, content_hash="0" * 64, size_kb=1, candidate_id="c1", attachment_id=seeded.cut.id))
    env.db.commit()
    assert script.run(env.engine, args(apply=True)) == 0
    assert versions(env, "passport") == [1, 2, 4, 5]


def test_the_candidate_must_exist_and_the_remote_guard_applies(env, monkeypatch, capsys):
    assert script.run(env.engine, args(candidate="CI-999")) == 1
    monkeypatch.delenv("CRM_ENV_FILE", raising=False)
    assert script.main(["--candidate", "CI-167"]) == 2 and "Refused" in capsys.readouterr().err
    assert script.main(["--candidate", "CI-167", "--all"]) == 2
    assert script.main([]) == 2
    assert script.main(["--candidate", "CI-167", "--database-url", "postgresql+psycopg://u:p@db.example.com/crm"]) == 2
