"""A rejected document can be judged again ("Read again") or, when only its NAME differed, confirmed by a person.

DB-backed against the guarded disposable PostgreSQL (TEST_DATABASE_URL), on the application-form API fixture.
"""
import json
import uuid

import pytest
from sqlalchemy import select

from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob, Role
from app.services import application_form, form_extraction as fx
from app.services.document_identity import IDENTITY_RESOLVED_ACTION, IDENTITY_REJECTED_ACTION, IDENTITY_REVIEW_MESSAGE
from test_application_form import as_user, desk, form, url  # noqa: F401  (fixtures and helpers)
from test_database_naming import _migrate, schema_database  # noqa: F401  (fixture)
from test_document_identity import _job_output, _passport_of, _resume_of


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    monkeypatch.setattr(application_form, "_attach_read_documents", lambda *args, **kwargs: {})
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [])


def _file(db, slot="resume", name="Alex_Example-Resume.pdf", candidate="candidate"):
    version = len(list(db.scalars(select(Attachment.id).where(Attachment.entity_id == candidate, Attachment.slot == slot)))) + 1
    attachment = Attachment(id=str(uuid.uuid4()), entity_type="candidate", entity_id=candidate, slot=slot, version=version, file_name=name,
                            size_kb=1, content_type="application/pdf", object_key="k/" + name, content_hash=uuid.uuid4().hex, uploaded_by="staff")
    db.add(attachment)
    db.flush()
    return attachment


def _job(db, attachment, output, candidate="candidate"):
    job = DocumentExtractionJob(id=str(uuid.uuid4()), owner_id="staff", group_id=str(uuid.uuid4()), kind=attachment.slot, filename=attachment.file_name,
                                content_type="application/pdf", object_key=attachment.object_key, content_hash=attachment.content_hash, size_kb=1,
                                status="completed", candidate_id=candidate, attachment_id=attachment.id, state={"output": json.loads(json.dumps(output, default=str))},
                                draft={"applicationForm": {"candidateId": candidate, "slot": attachment.slot, "attachmentId": attachment.id}})
    db.add(job)
    db.flush()
    return job


def reject(env, output, *, slot="resume", name="Alex_Example-Resume.pdf", before=None):
    """A document read and rejected exactly as the worker does it. Returns (attachment id, job id)."""
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        if before:
            before(candidate)
        attachment = _file(db, slot, name)
        job = _job(db, attachment, output)
        application_form.apply_job_output(db, job, output)
        assert job.status == "needs_review" and job.last_error == IDENTITY_REVIEW_MESSAGE
        db.commit()
        return attachment.id, job.id


def identity(env, attachment_id, action, candidate="candidate"):
    return env.client.post(url(candidate) + "/identity", json={"attachmentId": attachment_id, "action": action})


def audits(env, action):
    with env.sessions() as db:
        return [row.meta for row in db.scalars(select(AuditEntry).where(AuditEntry.action == action))]


def stranger_resume():
    return _job_output(resume=_resume_of("Zubair Hussain", personal_info={"father_name": "Ganesan"}))


def test_a_name_only_rejection_can_be_confirmed_and_only_that_reading_is_resolved(desk):
    attachment_id, job_id = reject(desk, stranger_resume())
    other_id, other_job = reject(desk, stranger_resume(), name="Second-Resume.pdf")
    rejected = form(desk)["documents"]["resume"]
    assert rejected["identityMismatch"] is True and rejected["extraction"]["identityReasons"] == [
        {"field": "name", "documentKind": "resume", "found": "Zubair Hussain", "expected": "Alex Example", "expectedFrom": "candidate"}]
    response = identity(desk, attachment_id, "confirm_name")
    assert response.status_code == 200, response.text
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.name == "Alex Example" and candidate.father_name == "Ganesan"          # the name stays; the rest is filled
        assert db.get(DocumentExtractionJob, job_id).status == "completed" and db.get(DocumentExtractionJob, other_job).status == "needs_review"
    resolved = [meta for meta in audits(desk, IDENTITY_RESOLVED_ACTION) if meta["attachment_id"] == attachment_id]
    assert len(resolved) == 1 and resolved[0]["document_job_id"] == job_id and resolved[0]["confirmed_by"] == "staff"
    assert not [meta for meta in audits(desk, IDENTITY_RESOLVED_ACTION) if meta["attachment_id"] == other_id]     # the other file stays wrong
    assert response.json()["documents"]["resume"]["uploaded"] is True
    assert identity(desk, attachment_id, "confirm_name").status_code == 409                                      # nothing is waiting any more


def test_confirming_a_stand_in_candidate_keeps_the_name_the_documents_gave(desk):
    def stand_in(candidate):
        candidate.name, candidate.field_sources = "rashid khan-alnoor", {fx.PROVISIONAL_NAME: True}
    output = _job_output(resume=_resume_of("Arun Kumar"), passport=_passport_of("BALA", "RAJAN", "K1234567"))
    attachment_id, job_id = reject(desk, output, before=stand_in)
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").name == "Bala Rajan"       # the passport's name replaced the stand-in at rejection
    assert identity(desk, attachment_id, "confirm_name").status_code == 200
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.name == "Bala Rajan" and candidate.passport_no == "K1234567"
        assert db.get(DocumentExtractionJob, job_id).status == "completed"


def test_a_passport_number_mismatch_can_never_be_confirmed(desk):
    output = _job_output(resume=_resume_of("Zubair Hussain", passport_details={"passport_number": "Z9999999"}))
    attachment_id, job_id = reject(desk, output, before=lambda candidate: setattr(candidate, "passport_no", "K1234567"))
    reasons = form(desk)["documents"]["resume"]["extraction"]["identityReasons"]
    assert {reason["field"] for reason in reasons} == {"name", "passport_no"}
    response = identity(desk, attachment_id, "confirm_name")
    assert response.status_code == 409 and "name alone" in response.json()["detail"]
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review" and db.get(Candidate, "candidate").father_name is None
    assert audits(desk, IDENTITY_RESOLVED_ACTION) == []


def test_a_duplicate_of_another_candidate_can_never_be_confirmed_and_read_again_keeps_saying_who(desk, monkeypatch):
    from types import SimpleNamespace
    other = SimpleNamespace(id="second-candidate", care_id="CI-157", name="Second-Candidate")
    monkeypatch.setattr("app.services.document_identity.identity_matches", lambda *args, **kwargs: [(other, ["phone number"])])
    attachment_id, job_id = reject(desk, stranger_resume())
    assert identity(desk, attachment_id, "confirm_name").status_code == 409
    again = identity(desk, attachment_id, "read_again")
    assert again.status_code == 200
    extraction = again.json()["documents"]["resume"]["extraction"]
    assert extraction["identityMismatch"] is True and extraction["duplicateOf"]["careId"] == "CI-157"
    assert {reason["field"] for reason in extraction["identityReasons"]} == {"candidate", "name"}
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review"
    assert audits(desk, IDENTITY_RESOLVED_ACTION) == []


def _ci_167(env):
    """The candidate as the bulk import leaves it: the folder stand-in as its name, every file stored under the stand-in's stem
    (the candidate's own documents and the application's stage copies, plus the stage's own references to them), the first
    resume reading rejected against the stand-in (an older rule) and nothing else read yet."""
    from app.db.models import Application, ApplicationStage
    stem = "rashid_khan_alnoor-"
    output = _job_output(resume=_resume_of("RASHIDKHAN NAWAZKHAN MANSUR"))
    with env.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        candidate.name, candidate.field_sources, candidate.needs_review = "rashid khan-alnoor", {fx.PROVISIONAL_NAME: True}, True
        resume = _file(db, "resume", stem + "Resume.pdf")
        passports = [_file(db, "passport", stem + name) for name in ("Passport.pdf", "Passport-2.pdf", "Passport-3.pdf")]
        aadhaar = _file(db, "aadhaar", stem + "Aadhaar_card.pdf")
        application = Application(id="app-1", candidate_id="candidate", order_id="order", owner_id="staff", current_stage=3)
        db.add(application)
        db.flush()
        copy = Attachment(id="stage-copy", entity_type="application", entity_id=application.id, slot="stage_2_file", version=1, file_name=stem + "Passport.pdf",
                          size_kb=1, content_type="application/pdf", object_key="k/stage", content_hash="stage", uploaded_by="staff")
        db.add(copy)
        db.add(ApplicationStage(application_id=application.id, stage=2, data={"passport": {"id": passports[0].id, "name": stem + "Passport.pdf"}}))
        application.draft = {"file": {"id": resume.id, "name": stem + "Resume.pdf"}}
        job = _job(db, resume, output)
        job.status, job.last_error = "needs_review", IDENTITY_REVIEW_MESSAGE
        job.draft = {"applicationForm": {**job.draft["applicationForm"], "result": {"status": "needs_review", "identityMismatch": True, "duplicateOf": None}}}
        db.add(AuditEntry(actor_id="staff", action=IDENTITY_REJECTED_ACTION, target=candidate.name, entity_type="candidate", entity_id="candidate",
                          meta={"attachment_id": resume.id, "document_job_id": job.id, "conflicting_fields": ["name"]}))
        db.commit()
        return resume.id, [item.id for item in passports], aadhaar.id, job.id


def _read_document(env, attachment_id, output, slot):
    """What the worker does when it finishes one stored file, with the reader's answer faked."""
    with env.sessions() as db:
        attachment = db.get(Attachment, attachment_id)
        job = _job(db, attachment, output)
        application_form.apply_job_output(db, job, output)
        db.commit()
        return job.id, job.status


def test_read_again_on_an_existing_stand_in_candidate_names_it_renames_everything_and_clears_the_note(desk):
    """CI-167: the owner presses Read again on the existing candidate; nothing is re-uploaded or re-imported."""
    from app.db.models import Application, ApplicationStage
    resume_id, passport_ids, aadhaar_id, job_id = _ci_167(desk)
    assert form(desk)["documents"]["resume"]["identityMismatch"] is True
    response = identity(desk, resume_id, "read_again")
    assert response.status_code == 200, response.text
    real = "Rashidkhan_Nawazkhan_Mansur-"
    with desk.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.name == "Rashidkhan Nawazkhan Mansur" and not fx.provisional_name(candidate)
        assert candidate.father_name is None and candidate.education and candidate.field_sources["personal.candidateName"] == "extracted"
        assert db.get(DocumentExtractionJob, job_id).status == "completed"
        assert db.get(Attachment, resume_id).file_name == real + "Resume.pdf"
        assert [db.get(Attachment, item).file_name for item in passport_ids] == [real + "Passport.pdf", real + "Passport-2.pdf", real + "Passport-3.pdf"]
        assert db.get(Attachment, aadhaar_id).file_name == real + "Aadhaar_card.pdf"
        assert db.get(Attachment, "stage-copy").file_name == real + "Passport.pdf"                      # the application's stage copy
        assert db.scalar(select(ApplicationStage.data)) == {"passport": {"id": passport_ids[0], "name": real + "Passport.pdf"}}   # the stage's reference
        assert db.get(Application, "app-1").draft == {"file": {"id": resume_id, "name": real + "Resume.pdf"}}
        assert not any("rashid_khan_alnoor" in name for name in db.scalars(select(Attachment.file_name)))
    resolved = audits(desk, IDENTITY_RESOLVED_ACTION)
    assert len(resolved) == 1 and resolved[0]["attachment_id"] == resume_id and resolved[0]["document_job_id"] == job_id
    document = response.json()["documents"]["resume"]
    assert document["identityMismatch"] is False and document["uploaded"] is True            # the red note is gone

    # the passport and Aadhaar read afterwards are compared with the REAL name
    passport = _job_output(passport=_passport_of("RASHID KHAN NAWAZKHAN", "MANSUR", "K1234567"))
    assert _read_document(desk, passport_ids[0], passport, "passport")[1] == "completed"
    with desk.sessions() as db:
        assert db.get(Candidate, "candidate").passport_no == "K1234567"
    stranger = _job_output(passport=_passport_of("BALA", "RAJAN", "K7654321"))
    other_job, status = _read_document(desk, passport_ids[1], stranger, "passport")
    assert status == "needs_review"
    with desk.sessions() as db:
        reasons = db.get(DocumentExtractionJob, other_job).draft["applicationForm"]["result"]["identityReasons"]
        assert {"field": "name", "documentKind": "passport", "found": "Bala Rajan",
                "expected": "Rashid Khan Nawazkhan Mansur", "expectedFrom": "candidate"} in reasons     # the passport's own spelling now names him
        assert {"field": "passport_no", "documentKind": "passport"} in reasons and len(reasons) == 2
        assert db.get(Candidate, "candidate").passport_no == "K1234567" and db.get(Candidate, "candidate").name == "Rashid Khan Nawazkhan Mansur"
    assert form(desk)["documents"]["passport"]["identityMismatch"] is True


def test_read_again_without_a_saved_reading_reads_the_stored_file_again(desk, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(instance_name="test"))
    resume_id, _passports, _aadhaar, job_id = _ci_167(desk)
    with desk.sessions() as db:
        old = db.get(DocumentExtractionJob, job_id)
        old.state = {}
        db.commit()
    response = identity(desk, resume_id, "read_again")
    assert response.status_code == 200, response.text
    with desk.sessions() as db:
        jobs = list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.attachment_id == resume_id)))
        assert len(jobs) == 2 and sorted(job.status for job in jobs) == ["needs_review", "pending"]
    resolved = audits(desk, IDENTITY_RESOLVED_ACTION)
    assert len(resolved) == 1 and resolved[0]["document_job_id"] == job_id and "superseded_by_job" in resolved[0]
    assert identity(desk, resume_id, "confirm_name").status_code == 409


def test_read_again_on_a_document_that_is_still_wrong_refreshes_the_reasons(desk):
    attachment_id, job_id = reject(desk, stranger_resume())
    again = identity(desk, attachment_id, "read_again")
    assert again.status_code == 200
    assert again.json()["documents"]["resume"]["extraction"]["identityReasons"][0]["found"] == "Zubair Hussain"
    with desk.sessions() as db:
        assert db.get(DocumentExtractionJob, job_id).status == "needs_review"


def test_only_someone_who_may_edit_the_candidate_can_check_and_unknown_documents_are_refused(desk):
    attachment_id, _job_id = reject(desk, stranger_resume())
    assert identity(desk, "nope", "confirm_name").status_code == 404
    with desk.sessions() as db:
        fine = _file(db, "resume", "Fine.pdf")
        db.commit()
        fine_id = fine.id
    assert identity(desk, fine_id, "confirm_name").status_code == 409          # never rejected
    as_user(desk, "other", Role.staff)
    assert identity(desk, attachment_id, "confirm_name").status_code == 403
    as_user(desk, "manager", Role.manager)
    assert identity(desk, attachment_id, "confirm_name").status_code == 200
    assert identity(desk, attachment_id, "bogus").status_code == 422
