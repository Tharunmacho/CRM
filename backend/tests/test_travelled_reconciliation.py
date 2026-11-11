import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import (Application, ApplicationStage, ApplicationStatus, Attachment, AttendanceCoverItem, AttendanceHRRequest, AuditEntry, Candidate,
    CandidateStatus, Client, DocumentExtractionJob, Notification, Order, Remark, Source, Task, TaskKind, User)
from app.services.document_storage import put_bytes
from scripts import reconcile_travelled_candidates as subject
from test_workflow_scheduler import workspace
from test_database_naming import schema_database


def prepare(db, tmp_path, *, dated=True):
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local")
    candidate = db.get(Candidate, "candidate")
    candidate.name = "Synthetic Person"
    candidate.current_address = "Manually confirmed address"
    candidate.field_sources = {"personal.currentAddress": "manual"}
    documents = []
    for kind in ("resume", "passport"):
        payload = (kind + " synthetic bytes").encode()
        key = put_bytes(settings, f"source/{kind}.pdf", payload)
        sha = hashlib.sha256(payload).hexdigest()
        db.add(Attachment(id=f"original-{kind}", entity_type="candidate", entity_id=candidate.id,
                          slot=kind, file_name=kind + ".pdf", object_key=key, content_hash=sha, version=1))
        documents.append({"person": 1, "path": f"Synthetic Person/{kind}.pdf", "localPath": str(tmp_path / key),
                          "sha256": sha, "bytes": len(payload), "kinds": [kind], "contentType": "application/pdf", "derived": False})
    placements = [{"folder": "Synthetic Person", "client": "Actual Employer", "job": "Welder", "country": "UAE",
                   "travel_date": "2026-01-02" if dated else None, "travelled": not dated}]
    db.flush()
    return settings, placements, documents


def counts(db):
    return {model.__name__: db.scalar(select(func.count()).select_from(model))
            for model in (Candidate, Client, Order, Application, ApplicationStage, Attachment, AuditEntry)}


@pytest.mark.parametrize("dated", [True, False])
def test_existing_candidate_becomes_deployed_without_invented_milestones_and_rerun_is_noop(workspace, tmp_path, dated):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path, dated=dated)
        db.add(Task(id="existing-call", kind=TaskKind.initial_call, title="Initial call", candidate_id="candidate", owner_id="owner"))
        db.flush()
        db.add(Remark(id="retained-note", task_id="existing-call", author_id="owner", body="Retain genuine history"))
        db.commit()
        before = counts(db)
        plan = subject.build_plan(db, placements, documents, {})
        assert counts(db) == before
        assert plan["people"][0]["candidateId"] == "candidate"
        result = subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, {}, plan, writers_stopped=True)
        db.commit()
        assert result["candidatesReconciled"] == 1 and result["documentsAdded"] == 0
        assert result["verifiedCounts"]["candidatesDeployed"] == result["verifiedCounts"]["applicationsDeployed"] == 1
        assert result["verifiedCounts"]["unknownTravelDates"] == (0 if dated else 1)
        candidate = db.get(Candidate, "candidate")
        assert candidate.status == CandidateStatus.deployed and candidate.current_address == "Manually confirmed address"
        stages = list(db.scalars(select(ApplicationStage)))
        assert len(stages) == 1 and stages[0].stage == 11
        assert stages[0].data == ({"travelDate": "2026-01-02"} if dated else {})
        assert stages[0].completed_at.year >= 2026  # recording time is not a fabricated travel date
        assert db.get(Task, "existing-call").cancelled_at and db.get(Remark, "retained-note").body == "Retain genuine history"
        application = db.scalar(select(Application))
        assert application.status == ApplicationStatus.deployed and application.current_stage == 11
        contact = db.scalar(select(Client))
        assert contact.phone is None and contact.email is None and "not recorded" in contact.notes
        after = counts(db)
        again = subject.build_plan(db, placements, documents, {})
        result = subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, {}, again, writers_stopped=True)
        db.commit()
        assert result["alreadyDeployed"] == 1 and counts(db) == after


def test_existing_untouched_import_application_corrected_in_place_preserving_old_stages(workspace, tmp_path):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        base = subject.build_plan(db, placements, documents, {})
        orders = subject.ensure_masters(db, base)
        old = next(iter(orders.values()))
        application = Application(id="existing-app", candidate_id="candidate", order_id=old.id, owner_id="owner", current_stage=8, lock_version=0)
        db.add(application); db.flush()
        db.add(ApplicationStage(id="retained-stage", application_id=application.id, stage=7, attempt=1, data={"collected": "yes"}, outcome="Collected"))
        db.add(AuditEntry(action="Imported candidate with documents and stage history", entity_type="application", entity_id=application.id, meta={"bulk_import": True}))
        db.commit()
        placements[0]["client"] = "Correct Employer"
        decisions = {"correctBulkApplicationIds": [application.id]}
        plan = subject.build_plan(db, placements, documents, decisions)
        subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, decisions, plan, writers_stopped=True)
        db.commit()
        assert db.scalar(select(func.count()).select_from(Application)) == 1
        assert application.order_id != old.id and application.status == ApplicationStatus.deployed
        assert db.get(ApplicationStage, "retained-stage").data == {"collected": "yes"}
        assert sorted(db.scalars(select(ApplicationStage.stage))) == [7, 11]


def test_changed_source_identity_rejects_a_stale_reviewed_plan(workspace, tmp_path):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        plan = subject.build_plan(db, placements, documents, {})
        documents[0]["sha256"] = "f" * 64
        with pytest.raises(subject.ReconciliationError, match="at least two"):
            subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, {}, plan, writers_stopped=True)
        assert db.scalar(select(func.count()).select_from(Application)) == 0


def test_processing_claim_after_review_blocks_import_without_losing_provider_checkpoint(workspace, tmp_path):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        db.commit()
        plan = subject.build_plan(db, placements, documents, {})
        db.add(DocumentExtractionJob(id="busy", instance="test", owner_id="owner", group_id="busy-group", kind="resume",
            filename="resume.pdf", content_type="application/pdf", object_key="source/resume.pdf", content_hash=documents[0]["sha256"],
            size_kb=1, status="processing", claimed_by="active-worker", candidate_id="candidate",
            state={"provider_jobs": {"resume": {"job_id": "accepted-provider-job"}}}))
        db.commit()
        with pytest.raises(subject.ReconciliationError, match="processing claims"):
            subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, {}, plan, writers_stopped=True)
        db.rollback()
        assert db.get(DocumentExtractionJob, "busy").state["provider_jobs"]["resume"]["job_id"] == "accepted-provider-job"
        assert db.scalar(select(func.count()).select_from(Application)) == 0


def add_alias(db, documents):
    candidate = db.get(Candidate, "candidate")
    alias = Candidate(id="alias", ref="CND-ALIAS", name=candidate.name, phone=candidate.phone, source=Source.other,
                      call_remarks="A note on the duplicate", field_sources={})
    db.add(alias); db.flush()
    for index, item in enumerate(documents):
        original = db.get(Attachment, "original-" + item["kinds"][0])
        db.add(Attachment(id=f"alias-doc-{index}", entity_type="candidate", entity_id=alias.id, slot=original.slot,
                          version=1, file_name=original.file_name, object_key=original.object_key, content_hash=original.content_hash))
    db.add(Task(id="alias-task", kind=TaskKind.initial_call, title="Imported initial call", candidate_id=alias.id, owner_id="owner"))
    db.flush()
    db.add(Remark(id="alias-remark", task_id="alias-task", author_id="owner", body="Do not lose this note"))
    db.add(DocumentExtractionJob(id="alias-job", instance="test", owner_id="owner", group_id="alias-group", kind="resume",
        filename="resume.pdf", content_type="application/pdf", object_key="source/resume.pdf", content_hash=documents[0]["sha256"],
        size_kb=1, status="failed", candidate_id=alias.id, attachment_id="alias-doc-0",
        draft={"applicationForm": {"candidateId": alias.id, "attachmentId": "alias-doc-0"}, "savedCandidateId": alias.id}))
    db.add(Notification(id="alias-notification", user_id="owner", kind="candidate_assignment", title="Assignment", link_to="/candidates/alias/application"))
    db.add(AuditEntry(id="alias-audit", actor_id="owner", action="Created candidate", entity_type="candidate", entity_id=alias.id))
    db.flush()
    return alias


def test_proven_duplicate_merge_preserves_documents_notes_jobs_and_links(workspace, tmp_path):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        add_alias(db, documents); db.commit()
        decisions = {"people": {"1": {"candidateId": "candidate", "mergeCandidateIds": ["alias"]}}}
        plan = subject.build_plan(db, placements, documents, decisions)
        subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, decisions, plan, writers_stopped=True)
        db.commit(); db.expire_all()
        assert db.get(Candidate, "alias") is None
        assert db.get(Candidate, "candidate").call_remarks == "A note on the duplicate"
        assert db.get(Remark, "alias-remark").body == "Do not lose this note"
        assert db.get(Task, "alias-task").candidate_id == "candidate"
        for number in (0, 1):
            row = db.get(Attachment, f"alias-doc-{number}")
            assert row.entity_id == "candidate" and row.version == 2
        job = db.get(DocumentExtractionJob, "alias-job")
        assert job.candidate_id == "candidate" and job.attachment_id == "alias-doc-0"
        assert job.draft["applicationForm"]["candidateId"] == job.draft["savedCandidateId"] == "candidate"
        assert db.get(Notification, "alias-notification").link_to == "/candidates/candidate/application"
        assert db.get(AuditEntry, "alias-audit").meta["originalCandidateId"] == "alias"
        after = counts(db)
        again = subject.build_plan(db, placements, documents, decisions)
        subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, decisions, again, writers_stopped=True)
        db.commit()
        assert counts(db) == after


@pytest.mark.parametrize("conflict", ["passport", "manual", "processing", "application"])
def test_conflicting_duplicate_identity_or_linked_work_blocks_all_writes(workspace, tmp_path, conflict):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        alias = add_alias(db, documents)
        canonical = db.get(Candidate, "candidate")
        if conflict == "passport":
            canonical.passport_no, alias.passport_no = "A1234567", "B7654321"
        elif conflict == "manual":
            alias.current_address, alias.field_sources = "Different manually entered address", {"personal.currentAddress": "manual"}
        elif conflict == "processing":
            db.get(DocumentExtractionJob, "alias-job").status = "processing"
        elif conflict == "application":
            db.flush()
            order = next(iter(subject.ensure_masters(db, {"masters": subject.master_plan(db, placements)}).values()))
            db.add(Application(candidate_id="alias", order_id=order.id))
        db.commit()
        before = counts(db)
        with pytest.raises(subject.ReconciliationError):
            subject.build_plan(db, placements, documents, {"people": {"1": {"candidateId": "candidate", "mergeCandidateIds": ["alias"]}}})
        assert counts(db) == before and db.get(Candidate, "alias") is not None


def test_shared_group_booking_hash_cannot_identify_a_person(workspace, tmp_path):
    with workspace() as db:
        _, placements, documents = prepare(db, tmp_path)
        documents += [{**row, "person": 2} for row in documents]
        placements += [{**placements[0], "folder": "Another Person"}]
        db.commit()
        with pytest.raises(subject.ReconciliationError, match="unique source hashes"):
            subject.build_plan(db, placements, documents, {})


def test_duplicate_unique_relationship_conflict_is_reported_before_reparenting(workspace, tmp_path):
    with workspace() as db:
        _, placements, documents = prepare(db, tmp_path)
        add_alias(db, documents)
        request = AttendanceHRRequest(id="leave", user_id="owner", request_key="unique-merge-test", attendance_date=date(2026, 1, 2),
                                      kind="paid_leave", reason="Synthetic leave")
        db.add(request); db.flush()
        for identifier in ("candidate", "alias"):
            db.add(AttendanceCoverItem(id="cover-" + identifier, request_id=request.id, candidate_id=identifier,
                                       from_user_id="owner", to_user_id="other", started_at=datetime.now(timezone.utc)))
        db.commit()
        with pytest.raises(subject.ReconciliationError, match="relationship conflict"):
            subject.build_plan(db, placements, documents, {"people": {"1": {"candidateId": "candidate", "mergeCandidateIds": ["alias"]}}})
        assert db.get(AttendanceCoverItem, "cover-alias").candidate_id == "alias"
        assert db.scalar(select(func.count()).select_from(AttendanceCoverItem)) == 2


def test_reviewed_pdf_pages_split_repeatably_and_photo_is_an_image(tmp_path):
    import pymupdf
    root = tmp_path / "sources"
    folder = root / "Synthetic Person"
    folder.mkdir(parents=True)
    placements = [{"folder": folder.name, "client": "Employer", "job": "Role", "country": "UAE", "travelled": True}]
    placement_file = root / "_placements.json"
    placement_file.write_text(json.dumps(placements))
    pdf = pymupdf.open()
    for page_number in range(3):
        page = pdf.new_page(); page.insert_text((72, 72), f"Synthetic page {page_number + 1}")
    path = folder / "Synthetic Person-Other.pdf"
    pdf.save(path); pdf.close()
    original = path.read_bytes()
    sha = hashlib.sha256(original).hexdigest()
    relative = path.relative_to(root).as_posix()
    inventory = {"summary": {"placementsSha256": hashlib.sha256(placement_file.read_bytes()).hexdigest()},
                 "files": [{"person": 1, "path": relative, "sha256": sha, "bytes": len(original)}]}
    reviewed = {"files": [{"person": 1, "path": relative, "sha256": sha,
                           "visuallyVerifiedPages": {"passport": [1, 2], "photo": [3]}}]}
    _, first = subject.source_documents(root, inventory, reviewed, tmp_path / "prepared")
    _, second = subject.source_documents(root, inventory, reviewed, tmp_path / "prepared")
    assert [row["sha256"] for row in first] == [row["sha256"] for row in second]
    assert path.read_bytes() == original
    passport, photo = first[1:]
    with pymupdf.open(passport["localPath"]) as selected:
        assert len(selected) == 2 and "Synthetic page 2" in selected[1].get_text()
    assert photo["contentType"] == "image/png" and Path(photo["localPath"]).read_bytes().startswith(b"\x89PNG")
    reviewed["files"][0]["visuallyVerifiedPages"]["photo"] = [2]
    with pytest.raises(subject.ReconciliationError, match="overlap"):
        subject.source_documents(root, inventory, reviewed, tmp_path / "prepared")


def test_reviewed_original_retention_preserves_shared_bundle_and_rejects_unreviewed_bytes(tmp_path):
    root = tmp_path / "organized"
    root.mkdir()
    originals = tmp_path / "originals"
    originals.mkdir()
    placements = [{"folder": "Person One"}, {"folder": "Person Two"}]
    placement_path = root / "_placements.json"
    placement_path.write_text(json.dumps(placements))
    original = originals / "group-ticket.pdf"
    original.write_bytes(b"shared ticket including baggage and fare terms")
    sha = hashlib.sha256(original.read_bytes()).hexdigest()
    inventory = {"summary": {"placementsSha256": hashlib.sha256(placement_path.read_bytes()).hexdigest()}, "files": []}
    retained = {"files": [{"path": original.name, "sha256": sha, "people": [1, 2], "reviewReason": "Retain common fare terms alongside individual tickets"}]}
    _, documents = subject.source_documents(root, inventory, retained_originals=retained, original_root=originals)
    assert [row["person"] for row in documents] == [1, 2]
    assert all(row["kinds"] == ["source_bundle"] and row["sha256"] == sha for row in documents)
    original.write_bytes(b"changed")
    with pytest.raises(subject.ReconciliationError, match="changed"):
        subject.source_documents(root, inventory, retained_originals=retained, original_root=originals)
    retained["files"][0]["path"] = "../outside.pdf"
    with pytest.raises(subject.ReconciliationError, match="invalid path"):
        subject.source_documents(root, inventory, retained_originals=retained, original_root=originals)


def test_wrong_filename_slot_corrected_and_new_verified_passport_queued_without_outbound_messages(workspace, tmp_path, monkeypatch):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        documents[0]["invalidOriginalSlots"] = ["resume", "signed_offer"]
        documents[0]["kinds"] = ["source_bundle"]
        data = b"independently verified passport pages"
        path = tmp_path / "verified-passport.pdf"
        path.write_bytes(data)
        documents.append({"person": 1, "path": path.name, "localPath": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                          "bytes": len(data), "kinds": ["passport"], "contentType": "application/pdf", "derived": True,
                          "sourceSha256": documents[1]["sha256"], "pages": [1]})
        from app.services import application_form
        queued = []
        monkeypatch.setattr(application_form, "queue_reading", lambda _db, _user, person, attachment, _media:
                            queued.append((person.id, attachment.slot, attachment.content_hash)))
        db.commit()
        plan = subject.build_plan(db, placements, documents, {})
        result = subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, {}, plan, writers_stopped=True)
        db.commit()
        assert result["documentSlotsCorrected"] == result["documentsAdded"] == 1
        assert db.get(Attachment, "original-resume").slot == "source_bundle"
        assert queued == [("candidate", "passport", hashlib.sha256(data).hexdigest())]
        assert not db.scalar(select(Attachment).where(Attachment.slot == "stage_5_file"))


def test_owned_document_hash_must_match_actual_storage_before_claiming_verified(workspace, tmp_path):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        db.commit()
        plan = subject.build_plan(db, placements, documents, {})
        Path(documents[0]["localPath"]).write_bytes(b"corrupted after review")
        with pytest.raises(subject.ReconciliationError, match="checksum"):
            subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, {}, plan, writers_stopped=True)


def test_reviewed_name_correction_requires_unchanged_passport_dob_and_document_evidence(workspace, tmp_path):
    with workspace() as db:
        settings, placements, documents = prepare(db, tmp_path)
        alias = add_alias(db, documents)
        canonical = db.get(Candidate, "candidate")
        canonical.name = "Older Abbreviation"
        canonical.passport_no, canonical.dob = "A1234567", date(1985, 6, 7)
        alias.passport_no, alias.dob = None, canonical.dob
        correction = {"expectedCanonicalName": canonical.name, "verifiedName": alias.name,
                      "verifiedPassport": canonical.passport_no, "verifiedDob": canonical.dob.isoformat(),
                      "sourceHashes": [row["sha256"] for row in documents], "reason": "Full name visually checked against original passport, visa and CV"}
        decisions = {"people": {"1": {"candidateId": canonical.id, "mergeCandidateIds": [alias.id], "nameCorrection": correction}}}
        db.commit()
        plan = subject.build_plan(db, placements, documents, decisions)
        correction["verifiedPassport"] = "B7654321"
        with pytest.raises(subject.ReconciliationError, match="passport and date"):
            subject.build_plan(db, placements, documents, decisions)
        correction["verifiedPassport"] = canonical.passport_no
        subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, decisions, plan, writers_stopped=True)
        db.commit()
        assert canonical.name == "Synthetic Person" and db.get(Candidate, "alias") is None
        audit = db.scalar(select(AuditEntry).where(AuditEntry.action == "Corrected historical identity from reviewed documents"))
        assert audit.meta["before"] == "Older Abbreviation" and audit.meta["after"] == "Synthetic Person"
        after = counts(db)
        again = subject.build_plan(db, placements, documents, decisions)
        subject.reconcile(db, db.get(User, "admin"), settings, placements, documents, decisions, again, writers_stopped=True)
        db.commit()
        assert counts(db) == after
