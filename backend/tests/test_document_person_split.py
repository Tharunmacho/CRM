"""Synthetic PDFs and disposable PostgreSQL only; no provider or mailbox calls."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import hashlib

import pymupdf as fitz
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.api.routers import document_bundles as api, document_jobs, mail_actions
from app.db.models import Attachment, Candidate, DocumentExtractionJob, DocumentPersonGroup, MultiPersonIntake, Role, Source, Task, User, MailAccount, MailMessage, MailAttachment, EmailIngestionJob, WhatsAppChannel, WhatsAppMessage
from app.integrations.ocr import inspection
from app.integrations.ocr.person_split import page_identity, propose_groups
from app.services import document_extraction, document_person_split as subject
from app.services.document_storage import put_bytes, read_bytes
from test_database_naming import _migrate, schema_database


def cv(name, phone):
    return f"Curriculum Vitae\nName: {name}\nPhone: {phone}\nProfessional Summary\nExperienced industrial electrician with international experience.\nWork Experience\nElectrical maintenance and installation for industrial projects.\nEducation\nDiploma in electrical engineering.\nSkills\nWiring, motor maintenance and testing."


def pdf(*texts):
    with fitz.open() as doc:
        for text in texts:
            page = doc.new_page()
            page.insert_text((50, 50), text, fontsize=10)
        return doc.tobytes()


@pytest.fixture
def env(schema_database, tmp_path, monkeypatch):
    _migrate(schema_database, "head")
    sessions = sessionmaker(bind=schema_database, autoflush=False, expire_on_commit=False)
    admin, staff, other = (str(uuid4()) for _ in range(3))
    with sessions() as db:
        db.add_all([User(id=admin, name="Admin", email="admin@example.test", role=Role.admin, active=True),
                    User(id=staff, name="Recruiter", email="staff@example.test", role=Role.staff, active=True, receives_auto_assign=True),
                    User(id=other, name="Other recruiter", email="other@example.test", role=Role.staff, active=True, receives_auto_assign=True)])
        db.commit()
    settings = SimpleNamespace(storage_dir=str(tmp_path), storage_backend="local", tesseract_cmd=None,
        ocr_languages="eng", ocr_max_pages=300, ocr_page_timeout_seconds=20, ocr_document_timeout_seconds=600,
        veris_ocr_base_url="", veris_ocr_api_key="")
    environment = SimpleNamespace(sessions=sessions, settings=settings, admin=admin, staff=staff, other=other,
        actor=SimpleNamespace(id=admin, role=Role.admin))
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(document_jobs, "get_settings", lambda: settings)
    monkeypatch.setattr(inspection, "find_tesseract", lambda *_: None)
    monkeypatch.setattr(document_extraction, "VerisOCR", lambda **_: pytest.fail("A mixed-person document must not reach a provider"))
    app = FastAPI(); app.include_router(api.router); app.include_router(document_jobs.router)
    app.include_router(mail_actions.router, prefix="/api/inbox")
    def database():
        with sessions() as db: yield db
    app.dependency_overrides[api.get_db] = database
    app.dependency_overrides[api.current_user] = lambda: environment.actor
    with TestClient(app) as client:
        environment.client = client
        yield environment


def prepare(env, texts=None):
    data = pdf(*(texts or [cv("Alice Kumar", "9876543210"), cv("Beena Devi", "9876543211")]))
    group_id, job_id = str(uuid4()), str(uuid4())
    digest = hashlib.sha256(data).hexdigest()
    key = put_bytes(env.settings, f"synthetic/{job_id}/original.pdf", data, "application/pdf")
    with env.sessions() as db:
        db.add(DocumentExtractionJob(id=job_id, owner_id=env.actor.id, group_id=group_id, kind="resume", filename="two-people.pdf",
            content_type="application/pdf", object_key=key, content_hash=digest, size_kb=1, status="uploaded"))
        db.commit()
    response = env.client.post("/api/document-bundles", json={"sourceType": "manual", "sourceId": group_id})
    assert response.status_code == 202, response.text
    first = response.json(); assert first["status"] == "pending"
    subject.process_bundle(first["id"], sessions=env.sessions, settings=env.settings)
    response = env.client.get("/api/document-bundles/" + first["id"])
    assert response.status_code == 200, response.text
    value = response.json(); assert value["status"] == "review", value
    value["sourceGroupId"], value["jobId"] = group_id, job_id
    return value


def payload(bundle):
    return deepcopy({"revision": bundle["revision"], "groups": [{key: group[key] for key in ("id", "label", "fields", "pages")} for group in bundle["groups"]], "ignoredPages": bundle["ignoredPages"]})


def commit(env, bundle, **extra):
    return env.client.post(f'/api/document-bundles/{bundle["id"]}/commit', json={"revision": bundle["revision"], "confirmed": True, **extra})


def test_detector_conservative_names_and_contact_boundaries():
    alice = page_identity(cv("Alice Kumar", "9876543210"))
    same = page_identity(cv("Alice Kumar", "+91 98765 43210"))
    other = page_identity(cv("Beena Devi", "9876543210"))
    assert alice["reliable"] and same["phones"] == alice["phones"]
    pages = [{"sourceId": "s", "pageNumber": i + 1, "kind": "resume", "status": "read", "identity": item} for i, item in enumerate([alice, same, other, page_identity("Education and experience continued")])]
    groups = propose_groups(pages)
    assert [len(group["pages"]) for group in groups] == [2, 1]
    assert page_identity(cv("Alice Kumar", "9876543210") + "\nName: Beena Devi\nMobile: 9876543211")["mixed"]


def test_multi_person_guard_precedes_provider_and_survives_checkpoint(env):
    data = pdf(cv("Alice Kumar", "9876543210"), cv("Beena Devi", "9876543211"))
    state = {}; saved = []
    value = document_extraction.extract_attachment(data, state=state, save=lambda value: saved.append(value), settings=env.settings)
    assert value["status"] == "needs_review" and value["splitSuggested"]
    assert not value["extractions"] and saved[-1]["splitSuggested"]
    repeated = document_extraction.extract_attachment(data, state=deepcopy(saved[-1]), save=lambda _: None, settings=env.settings)
    assert repeated["splitSuggested"]


def test_two_people_atomic_commit_correct_pages_tasks_and_replay(env):
    bundle = prepare(env)
    assert len(bundle["groups"]) == 2 and len(bundle["pages"]) == 2
    response = commit(env, bundle)
    assert response.status_code == 200, response.text
    saved = response.json(); assert saved["status"] == "completed" and len(saved["candidateIds"]) == 2
    again = commit(env, bundle)
    assert again.status_code == 200 and again.json()["candidateIds"] == saved["candidateIds"]
    with env.sessions() as db:
        candidates = list(db.scalars(select(Candidate).order_by(Candidate.name)))
        assert len(candidates) == 2 and {row.owner_id for row in candidates} == {env.staff, env.other}
        tasks = list(db.scalars(select(Task)))
        assert len(tasks) == 2 and all((task.due_at - task.sla_started_at).total_seconds() == 48 * 3600 for task in tasks)
        attachments = list(db.scalars(select(Attachment)))
        assert len(attachments) == 4
        for candidate in candidates:
            own = [item for item in attachments if item.entity_id == candidate.id]
            assert {item.slot for item in own} == {"source_bundle", "resume"}
            for attachment in own:
                with fitz.open(stream=read_bytes(env.settings, attachment.object_key), filetype="pdf") as source:
                    assert source.page_count == 1
                    text = source[0].get_text()
                    assert candidate.name in text
                    assert next(item.name for item in candidates if item.id != candidate.id) not in text
            assert candidate.ocr_data["person_split"]["bundleId"] == bundle["id"]
        job = db.get(DocumentExtractionJob, bundle["jobId"])
        assert job.status == "attached" and job.candidate_id is None and len(job.draft["splitCandidateIds"]) == 2
    assert env.client.post(f'/api/ocr/jobs/{bundle["jobId"]}/attach', json={"candidateId": saved["candidateIds"][0]}).status_code == 409
    assert env.client.post(f'/api/ocr/jobs/{bundle["jobId"]}/retry').status_code == 409


def test_manual_owner_scope_and_authenticated_previews(env):
    bundle = prepare(env)
    preview = env.client.get(bundle["pages"][0]["previewUrl"])
    assert preview.status_code == 200 and "private" in preview.headers["cache-control"]
    with fitz.open(stream=preview.content, filetype="pdf") as page: assert page.page_count == 1
    env.actor = SimpleNamespace(id=env.other, role=Role.staff)
    assert env.client.get('/api/document-bundles/' + bundle["id"]).status_code == 404
    assert env.client.get(bundle["pages"][0]["previewUrl"]).status_code == 404
    assert commit(env, bundle).status_code == 404
    assert env.client.post('/api/document-bundles', json={"sourceType": "manual", "sourceId": bundle["sourceGroupId"]}).status_code == 404


def test_partial_save_recovery_revision_and_page_overlap_validation(env):
    bundle = prepare(env)
    data = payload(bundle); page = data["groups"][0]["pages"].pop()
    response = env.client.put(f'/api/document-bundles/{bundle["id"]}/review', json=data)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert commit(env, saved).status_code == 422
    assert env.client.put(f'/api/document-bundles/{bundle["id"]}/review', json=data).status_code == 409
    overlap = payload(saved); overlap["groups"][0]["pages"] = deepcopy(overlap["groups"][1]["pages"])
    assert env.client.put(f'/api/document-bundles/{bundle["id"]}/review', json=overlap).status_code == 422
    fixed = payload(saved); fixed["groups"][0]["pages"] = [page]
    response = env.client.put(f'/api/document-bundles/{bundle["id"]}/review', json=fixed)
    assert response.status_code == 200 and commit(env, response.json()).status_code == 200


def test_mixed_single_page_requires_exclusion_and_cannot_be_assigned(env):
    bundle = prepare(env, [cv("Alice Kumar", "9876543210"), cv("Beena Devi", "9876543211"), "Name: Alice Kumar\nPhone: 9876543210\nName: Beena Devi\nPhone: 9876543211\nThis is an agency cover sheet containing multiple people."])
    mixed = bundle["pages"][2]; assert mixed["blocked"]
    data = payload(bundle); data["groups"][0]["pages"].append({key: mixed[key] for key in ("sourceId", "pageNumber")} | {"kind": "source_bundle"})
    assert env.client.put(f'/api/document-bundles/{bundle["id"]}/review', json=data).status_code == 422
    data = payload(bundle); data["ignoredPages"] = [{"sourceId": mixed["sourceId"], "pageNumber": 3, "reason": "Agency sheet; contains two people"}]
    saved = env.client.put(f'/api/document-bundles/{bundle["id"]}/review', json=data)
    assert saved.status_code == 200 and commit(env, saved.json()).status_code == 200


def test_duplicate_requires_explicit_merge_and_retains_owner_work(env):
    bundle = prepare(env)
    identifier = str(uuid4())
    with env.sessions() as db:
        db.add(Candidate(id=identifier, ref="EXISTING", name="Human verified Alice", phone="9876543210", source=Source.walk_in,
            owner_id=env.staff, skills=["Human verified skill"], total_experience=8))
        db.commit()
    first = commit(env, bundle)
    assert first.status_code == 409, first.text
    duplicates = first.json()["detail"]["duplicates"]
    assert len(duplicates) == 1 and duplicates[0]["candidateId"] == identifier
    choice = {"groupId": duplicates[0]["groupId"], "candidateId": identifier}
    response = commit(env, bundle, duplicateDecisions=[choice]); assert response.status_code == 200, response.text
    with env.sessions() as db:
        existing = db.get(Candidate, identifier)
        assert existing.owner_id == env.staff and existing.name == "Human verified Alice" and existing.skills == ["Human verified skill"] and existing.total_experience == 8
        assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_any_recruiter_can_combine_a_duplicate_owned_by_someone_else(env):
    env.actor = SimpleNamespace(id=env.other, role=Role.staff)
    bundle = prepare(env)
    identifier = str(uuid4())
    with env.sessions() as db:
        db.add(Candidate(id=identifier, ref="EXISTING", name="Human verified Alice", phone="9876543210", source=Source.walk_in,
            owner_id=env.staff, skills=["Human verified skill"], total_experience=8))
        db.commit()
    first = commit(env, bundle)
    assert first.status_code == 409, first.text
    duplicates = first.json()["detail"]["duplicates"]
    assert len(duplicates) == 1 and duplicates[0]["canMerge"] is True
    choice = {"groupId": duplicates[0]["groupId"], "candidateId": identifier}
    response = commit(env, bundle, duplicateDecisions=[choice]); assert response.status_code == 200, response.text
    with env.sessions() as db:
        existing = db.get(Candidate, identifier)
        assert existing.owner_id == env.staff and existing.name == "Human verified Alice" and existing.skills == ["Human verified skill"]
        assert db.scalar(select(func.count()).select_from(Task)) == 1


def test_storage_failure_rolls_back_every_person_and_retry_is_safe(env, monkeypatch):
    bundle = prepare(env)
    original = subject._attach_group
    calls = []
    def fail_second(*args):
        calls.append(1)
        if len(calls) == 2: raise OSError("synthetic disk failure")
        return original(*args)
    monkeypatch.setattr(subject, "_attach_group", fail_second)
    failed = commit(env, bundle); assert failed.status_code == 503
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
        assert db.get(MultiPersonIntake, bundle["id"]).status == "review"
        assert all(group.status == "draft" for group in db.scalars(select(DocumentPersonGroup)))
    monkeypatch.setattr(subject, "_attach_group", original)
    assert commit(env, bundle).status_code == 200


def test_concurrent_confirm_serializes_into_one_result(env):
    bundle = prepare(env)
    def run():
        with env.sessions() as db:
            return subject.commit_review(db, bundle["id"], env.actor, bundle["revision"], {}, env.settings)["candidateIds"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: run(), [1, 2]))
    assert first == second and len(first) == 2
    with env.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Candidate)) == 2
        assert db.scalar(select(func.count()).select_from(Task)) == 2


def test_detection_blocks_legacy_attachment_even_without_review(env):
    bundle = prepare(env)
    with env.sessions() as db:
        db.query(DocumentPersonGroup).delete(); db.query(MultiPersonIntake).delete()
        job = db.get(DocumentExtractionJob, bundle["jobId"])
        job.state = {"splitSuggested": True}; db.commit()
        with pytest.raises(HTTPException) as error:
            subject.ensure_single_person_source(db, "manual", bundle["sourceGroupId"])
        assert error.value.status_code == 409 and error.value.detail["splitSuggested"]


@pytest.mark.parametrize("kind", ["email", "whatsapp"])
def test_shared_source_admin_review_completion_and_staff_forbidden(env, kind):
    data = pdf(cv("Alice Kumar", "9876543210"), cv("Beena Devi", "9876543211"))
    identifier = str(uuid4()); digest = hashlib.sha256(data).hexdigest()
    key = put_bytes(env.settings, f"external/{identifier}.pdf", data, "application/pdf")
    with env.sessions() as db:
        if kind == "email":
            account = MailAccount(address="jobs@example.test", provider="imap")
            db.add(account); db.flush()
            db.add(MailMessage(id=identifier, account_id=account.id, provider_message_id="synthetic", received_at=datetime.now(timezone.utc), from_address="sender@example.test")); db.flush()
            db.add(MailAttachment(message_id=identifier, file_name="people.pdf", object_key=key, content_hash=digest))
            db.add(EmailIngestionJob(message_id=identifier, status="needs_review"))
        else:
            db.add(WhatsAppChannel(phone_number_id="synthetic-business", enabled=True)); db.flush()
            db.add(WhatsAppMessage(id=identifier, provider_message_id="synthetic", phone_number_id="synthetic-business", sender_number="919999999999", received_at=datetime.now(timezone.utc), message_type="document", file_name="people.pdf", object_key=key, content_hash=digest, content_type="application/pdf", status="needs_review"))
        db.commit()
    env.actor = SimpleNamespace(id=env.staff, role=Role.staff)
    assert env.client.post('/api/document-bundles', json={"sourceType": kind, "sourceId": identifier}).status_code == 403
    env.actor = SimpleNamespace(id=env.admin, role=Role.admin)
    response = env.client.post('/api/document-bundles', json={"sourceType": kind, "sourceId": identifier})
    assert response.status_code == 202, response.text
    review = response.json(); subject.process_bundle(review["id"], sessions=env.sessions, settings=env.settings)
    review = env.client.get('/api/document-bundles/' + review["id"]).json()
    assert review["status"] == "review"
    env.actor = SimpleNamespace(id=env.staff, role=Role.staff)
    assert env.client.get('/api/document-bundles/' + review["id"]).status_code == 200
    assert env.client.put('/api/document-bundles/' + review["id"] + '/review', json=payload(review)).status_code == 403
    assert commit(env, review).status_code == 403
    env.actor = SimpleNamespace(id=env.admin, role=Role.admin)
    saved = commit(env, review); assert saved.status_code == 200, saved.text
    assert len(saved.json()["candidateIds"]) == 2
    if kind == "email":
        for candidate_id in saved.json()["candidateIds"]:
            history = env.client.get(f'/api/inbox/candidates/{candidate_id}/evidence')
            assert history.status_code == 200 and [item["messageId"] for item in history.json()] == [identifier]
        with env.sessions() as db:
            unrelated = Candidate(ref="UNRELATED", name="Another person", phone="9876500000", source=Source.walk_in)
            db.add(unrelated); db.commit(); unrelated_id = unrelated.id
        assert env.client.get(f'/api/inbox/candidates/{unrelated_id}/evidence').json() == []
    with env.sessions() as db:
        assert {candidate.source.value for candidate in db.scalars(select(Candidate).where(Candidate.id.in_(saved.json()["candidateIds"])))} == {kind}
        if kind == "email":
            receipt = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == identifier))
            assert db.get(MailMessage, identifier).candidate_id is None
        else:
            receipt = db.get(WhatsAppMessage, identifier)
        assert receipt.status == "completed" and receipt.candidate_id is None
        with pytest.raises(HTTPException): subject.ensure_single_person_source(db, kind, identifier)


def test_manual_referral_source_and_dismissed_evidence_preserved_correctly(env):
    bundle = prepare(env)
    data = payload(bundle)
    for group in data["groups"]: group["fields"]["source"] = "referral"
    saved = env.client.put('/api/document-bundles/' + bundle["id"] + '/review', json=data)
    assert commit(env, saved.json()).status_code == 200
    with env.sessions() as db:
        assert {candidate.source for candidate in db.scalars(select(Candidate))} == {Source.referral}
        group_id = str(uuid4())
        db.add(DocumentExtractionJob(owner_id=env.admin, group_id=group_id, kind="resume", filename="removed.pdf", object_key="unused", content_hash="x" * 64,
            content_type="application/pdf", size_kb=1, status="dismissed", state={"splitSuggested": True}))
        db.flush()
        subject.ensure_single_person_source(db, "manual", group_id)


def test_reserved_manual_worker_does_not_restart_extraction(env, monkeypatch):
    from app.services import manual_extraction
    bundle = prepare(env)
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **kw: pytest.fail("Reserved review must not restart the single-person worker"))
    manual_extraction.process_job(bundle["jobId"], sessions=env.sessions, settings=env.settings)
    with env.sessions() as db:
        assert db.get(DocumentExtractionJob, bundle["jobId"]).status == "needs_review"


def test_provider_acceptance_checkpoint_survives_review_reservation(env, monkeypatch):
    from app.services import manual_extraction
    data = pdf(cv("Alice Kumar", "9876543210"))
    group_id, job_id = str(uuid4()), str(uuid4())
    digest = hashlib.sha256(data).hexdigest()
    key = put_bytes(env.settings, "synthetic/acceptance.pdf", data, "application/pdf")
    with env.sessions() as db:
        db.add(DocumentExtractionJob(id=job_id, owner_id=env.admin, group_id=group_id, kind="resume", filename="single.pdf", content_type="application/pdf", object_key=key, content_hash=digest, size_kb=1, status="pending"))
        db.commit()
    def accepted_then_reviewed(data, *, state, save, **kwargs):
        state["provider_jobs"] = {"resume": {"job_id": "synthetic-accepted-job", "status": "queued"}}
        response = env.client.post('/api/document-bundles', json={"sourceType": "manual", "sourceId": group_id})
        assert response.status_code == 202
        save(state)
        pytest.fail("A reserved source must not continue the automatic pipeline")
    monkeypatch.setattr(manual_extraction, "extract_attachment", accepted_then_reviewed)
    manual_extraction.process_job(job_id, sessions=env.sessions, settings=env.settings)
    with env.sessions() as db:
        job = db.get(DocumentExtractionJob, job_id)
        assert job.status == "needs_review"
        assert job.state["provider_jobs"]["resume"]["job_id"] == "synthetic-accepted-job"
        assert db.scalar(select(func.count()).select_from(Candidate)) == 0


def test_completed_manual_history_recovers_exact_original_for_uploader_only(env):
    env.actor = SimpleNamespace(id=env.staff, role=Role.staff)
    bundle = prepare(env)
    with env.sessions() as db:
        original = read_bytes(env.settings, db.get(DocumentExtractionJob, bundle["jobId"]).object_key)
    result = commit(env, bundle); assert result.status_code == 200, result.text
    saved = result.json()
    # The private history remains discoverable even after normal pending-intake
    # recovery has removed this successfully attached source.
    assert env.client.get('/api/ocr/jobs').json() == []
    for candidate_id in saved["candidateIds"]:
        response = env.client.get(f'/api/document-bundles/candidates/{candidate_id}/sources')
        assert response.status_code == 200 and "private" in response.headers["cache-control"]
        assert not response.json()["hasMore"]
        history = response.json()["reviews"]
        assert len(history) == 1 and history[0]["sourceId"] == bundle["sourceGroupId"]
        download = env.client.get(history[0]["originals"][0]["url"] + '?download=true')
        assert download.status_code == 200 and download.content == original
        assert 'attachment' in download.headers['content-disposition']
        assert 'no-store' in download.headers['cache-control']
    reopened = env.client.post('/api/document-bundles', json={"sourceType": "manual", "sourceId": bundle["sourceGroupId"]})
    assert reopened.status_code == 202 and reopened.json()["status"] == "completed"
    assert env.client.get(reopened.json()["sources"][0]["originalUrl"]).content == original
    with env.sessions() as db:
        unrelated = Candidate(ref="UNRELATED-MANUAL", name="Unrelated profile", phone="9786500000", source=Source.walk_in)
        db.add(unrelated); db.commit(); unrelated_id = unrelated.id
    assert env.client.get(f'/api/document-bundles/candidates/{unrelated_id}/sources').json()["reviews"] == []
    for identifier, role in [(env.other, Role.staff), (env.admin, Role.admin)]:
        env.actor = SimpleNamespace(id=identifier, role=role)
        assert env.client.get(f'/api/document-bundles/candidates/{saved["candidateIds"][0]}/sources').json()["reviews"] == []
        assert env.client.get(history[0]["originals"][0]["url"]).status_code == 404
        assert env.client.get('/api/document-bundles/' + bundle["id"]).status_code == 404


# ---- reviewed groups are read with the normal extraction; never-ingested emails are reachable -----

def fake_provider(monkeypatch, submissions):
    from test_document_extraction import result

    class Jobs:
        def submit(self, document, *, mode, idempotency_key):
            with fitz.open(stream=document.data, filetype="pdf") as sent:
                submissions.append((mode, sent.page_count, idempotency_key))
            return SimpleNamespace(job_id=f"job-{len(submissions)}-{mode}", status="queued")

        def get(self, job_id):
            mode = job_id.split("-", 2)[2]
            count = next(pages for kind, pages, _ in submissions if kind == mode)
            return SimpleNamespace(status="succeeded", result=result(mode, count))

    class Provider:
        def __init__(self, **_):
            self.jobs = Jobs()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(document_extraction, "VerisOCR", Provider)


def test_confirmed_people_are_read_by_the_normal_extraction_and_reviewer_values_win(env, monkeypatch):
    env.settings.veris_ocr_base_url, env.settings.veris_ocr_api_key = "https://ocr.invalid", "synthetic-key"
    submissions = []
    fake_provider(monkeypatch, submissions)
    bundle = prepare(env)
    response = commit(env, bundle)
    assert response.status_code == 200, response.text
    assert len(submissions) == 2 and {mode for mode, _, _ in submissions} == {"resume"}
    with env.sessions() as db:
        people = list(db.scalars(select(Candidate).where(Candidate.id.in_(response.json()["candidateIds"]))))
        assert sorted(person.name for person in people) == ["Alice Kumar", "Beena Devi"]
        for person in people:
            # Experience came from the document; the name stayed what the reviewer entered.
            assert person.total_experience == 3
            assert person.ocr_data["resume"]["request_id"] == "synthetic-resume"
            assert person.ocr_data["person_split"]["groupId"] and "extractionWarnings" not in person.ocr_data["person_split"]
    again = commit(env, bundle)
    assert again.status_code == 200 and len(submissions) == 2


def test_people_are_still_saved_with_a_visible_warning_when_reading_is_not_configured(env):
    bundle = prepare(env)
    response = commit(env, bundle)
    assert response.status_code == 200, response.text
    with env.sessions() as db:
        for person in db.scalars(select(Candidate).where(Candidate.id.in_(response.json()["candidateIds"]))):
            assert person.needs_review
            assert any("not set up" in note for note in person.ocr_data["person_split"]["extractionWarnings"])


def test_pending_provider_job_asks_for_the_same_confirmation_again(env, monkeypatch):
    env.settings.veris_ocr_base_url, env.settings.veris_ocr_api_key = "https://ocr.invalid", "synthetic-key"
    submissions = []
    fake_provider(monkeypatch, submissions)
    original_get = document_extraction.VerisOCR.__init__
    bundle = prepare(env)
    waiting = {"on": True}

    class Slow(document_extraction.VerisOCR):
        def __init__(self, **options):
            super().__init__(**options)
            real = self.jobs.get
            self.jobs.get = lambda job_id: SimpleNamespace(status="running", result=None) if waiting["on"] else real(job_id)

    monkeypatch.setattr(document_extraction, "VerisOCR", Slow)
    first = commit(env, bundle)
    assert first.status_code == 409 and first.json()["detail"]["code"] == "extraction_pending"
    waiting["on"] = False
    count = len(submissions)
    second = commit(env, bundle)
    assert second.status_code == 200 and len(submissions) == count


def test_never_ingested_email_can_be_reviewed_and_a_disconnected_mailbox_is_refused(env):
    data = pdf(cv("Alice Kumar", "9876543210"), cv("Beena Devi", "9876543211"))
    digest = hashlib.sha256(data).hexdigest()
    with env.sessions() as db:
        account = MailAccount(address="jobs@example.test", provider="imap")
        db.add(account); db.flush()
        for identifier, attach in (("11111111-1111-4111-8111-111111111111", True), ("22222222-2222-4222-8222-222222222222", False)):
            db.add(MailMessage(id=identifier, account_id=account.id, provider_message_id=identifier, received_at=datetime.now(timezone.utc)))
            db.flush()
            if attach:
                key = put_bytes(env.settings, f"external/{identifier}.pdf", data, "application/pdf")
                db.add(MailAttachment(message_id=identifier, file_name="people.pdf", object_key=key, content_hash=digest))
        db.commit()
    started = env.client.post("/api/document-bundles", json={"sourceType": "email", "sourceId": "11111111-1111-4111-8111-111111111111"})
    assert started.status_code == 202, started.text
    with env.sessions() as db:
        job = db.scalar(select(EmailIngestionJob).where(EmailIngestionJob.message_id == "11111111-1111-4111-8111-111111111111"))
        assert job is not None and job.status == "needs_review"
    nothing = env.client.post("/api/document-bundles", json={"sourceType": "email", "sourceId": "22222222-2222-4222-8222-222222222222"})
    assert nothing.status_code == 409
