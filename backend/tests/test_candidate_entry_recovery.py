"""Candidate saves and document recovery share one transaction and one identity."""
from uuid import uuid4

from sqlalchemy import func, select

from app.api.routers import candidates, document_jobs
from app.db import models as m
from test_candidate_workflows import db, make_client
from test_database_naming import schema_database
from auto_assign import round_robin_on  # noqa: F401  (fixture: these tests exercise the round-robin, which is opt-in)
import pytest as _pytest

pytestmark = _pytest.mark.usefixtures("round_robin_on")


def intake(db, owner='admin', *, draft=None):
    group = str(uuid4())
    job = m.DocumentExtractionJob(owner_id=owner, group_id=group, kind='resume', filename='synthetic-resume.pdf',
        content_type='application/pdf', object_key='synthetic/unread-original', content_hash=uuid4().hex * 2,
        size_kb=1, status='uploaded', draft=draft or {})
    db.add(job); db.commit()
    return job


def client(db, who='admin'):
    result = make_client(db, who)
    result.app.include_router(document_jobs.router)
    return result


def test_creation_binds_intake_before_response_and_retry_does_not_repeat_assignment(db):
    job = intake(db, draft={'form': {'name': 'Reviewed original', 'phone': '9000000020'}, 'editedFields': ['name', 'phone']})
    body = {'name': 'Reviewed original', 'phone': '9000000020', 'intake_group_id': job.group_id}
    api = client(db)
    first = api.post('/api/candidates', json=body)
    assert first.status_code == 201, first.text
    saved = first.json()
    db.expire_all()
    assert db.get(m.DocumentExtractionJob, job.id).draft['savedCandidateId'] == saved['id']
    # The first HTTP response may be lost after commit. Replaying the group is
    # idempotent even if a stale browser form now contains a different identity.
    second = api.post('/api/candidates', json={**body, 'name': 'Must not replace', 'phone': '9000000021'})
    assert second.status_code == 201 and second.json()['id'] == saved['id']
    assert second.json()['taskId'] == saved['taskId']
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(m.Candidate)) == 1
    assert db.scalar(select(func.count()).select_from(m.Task)) == 1
    assert db.get(m.Candidate, saved['id']).name == 'Reviewed original'
    assert db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == 'Created candidate')) == 1


def test_late_form_backup_preserves_saved_binding_and_other_intakes_stay_separate(db):
    first = intake(db, draft={'form': {'name': 'First candidate'}})
    other = intake(db, draft={'form': {'name': 'Second candidate'}})
    api = client(db)
    saved = api.post('/api/candidates', json={'name': 'First candidate', 'phone': '9000000020', 'intake_group_id': first.group_id}).json()
    response = api.put(f'/api/ocr/jobs/groups/{first.group_id}/draft', json={
        'form': {'name': 'Late browser snapshot'}, 'editedFields': ['name'], 'jobIds': [first.id], 'savedCandidateId': None,
    })
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(m.DocumentExtractionJob, first.id).draft['savedCandidateId'] == saved['id']
    assert db.get(m.DocumentExtractionJob, other.id).draft == {'form': {'name': 'Second candidate'}}
    recovered = api.get(f'/api/ocr/jobs/groups/{first.group_id}').json()
    assert recovered[0]['draft']['savedCandidateId'] == saved['id']
    assert recovered[0]['status'] == 'uploaded'
    assert api.put(f'/api/ocr/jobs/groups/{first.group_id}/draft', json={'savedCandidateId': 'different'}).status_code == 409


def test_draft_accepts_every_field_of_the_add_candidate_form(db):
    # The browser autosaves its whole form; one field the server does not list rejects the entire save.
    job = intake(db)
    form = {key: '1' for key in ('name', 'phone', 'whatsapp', 'email', 'dob', 'gender', 'nationality', 'currentCity', 'languages',
            'passportNo', 'passportIssue', 'passportExpiry', 'passportPlace', 'designation', 'totalExperience',
            'gulfExperience', 'skills', 'expectedSalary', 'source', 'sourceDetail')}
    response = client(db).put(f'/api/ocr/jobs/groups/{job.group_id}/draft',
        json={'form': form, 'editedFields': ['sourceDetail'], 'jobIds': [job.id], 'savedCandidateId': None})
    assert response.status_code == 200, response.text


def test_a_file_that_gave_no_name_or_contact_creates_no_candidate_row(db):
    job = intake(db)
    api = client(db)
    refused = api.post('/api/candidates', json={'intake_group_id': job.group_id})
    assert refused.status_code == 422
    assert 'could not read a name' in refused.json()['detail']
    # The old placeholder is never accepted as a name, and a name alone is not an identity.
    assert api.post('/api/candidates', json={'name': 'Name needs review', 'phone': '9000000020', 'intake_group_id': job.group_id}).status_code == 422
    assert api.post('/api/candidates', json={'name': 'Only A Name', 'intake_group_id': job.group_id}).status_code == 422
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(m.Candidate)) == 0
    assert db.scalar(select(func.count()).select_from(m.Task)) == 0
    # The saved upload stays available so a person can type the details.
    assert db.get(m.DocumentExtractionJob, job.id).status == 'uploaded'
    assert 'savedCandidateId' not in (db.get(m.DocumentExtractionJob, job.id).draft or {})


def test_name_and_email_from_a_document_creates_a_candidate_flagged_for_review(db):
    job = intake(db)
    api = client(db)
    created = api.post('/api/candidates', json={'name': 'Email Only', 'email': 'email.only@example.test', 'intake_group_id': job.group_id})
    assert created.status_code == 201, created.text
    db.expire_all()
    saved = db.get(m.Candidate, created.json()['id'])
    assert (saved.name, saved.phone) == ('Email Only', '')
    assert saved.needs_review and saved.status == m.CandidateStatus.needs_review
    # A number that is given must still be a real one.
    other = intake(db)
    assert api.post('/api/candidates', json={'name': 'Bad Number', 'phone': '12', 'intake_group_id': other.group_id}).status_code == 422


def test_name_and_phone_from_a_document_creates_a_ready_candidate(db):
    job = intake(db)
    created = client(db).post('/api/candidates', json={'name': 'Phone Person', 'phone': '9000000020', 'intake_group_id': job.group_id})
    assert created.status_code == 201, created.text
    db.expire_all()
    assert db.get(m.Candidate, created.json()['id']).name == 'Phone Person'


def test_internal_reviewed_current_intake_can_defer_assignment(db):
    """A verified packet may enter Data Bank before a recruiter is allocated."""
    job = intake(db)
    body = candidates.CandidateCreate(name='Waiting review', phone='9000000098', intake_group_id=job.group_id)

    saved = candidates.create_candidate_record(body, db, db.get(m.User, 'admin'), defer_assignment=True)

    db.expire_all()
    candidate = db.get(m.Candidate, saved.id)
    assert candidate.owner_id is None and candidate.intake_owner_id is None
    assert candidate.needs_review and candidate.status == m.CandidateStatus.needs_review
    assert db.scalar(select(func.count()).select_from(m.Task).where(m.Task.candidate_id == candidate.id)) == 0
    assert db.get(m.DocumentExtractionJob, job.id).draft['savedCandidateId'] == candidate.id


def test_passport_only_document_with_a_name_creates_a_candidate_and_without_a_name_does_not(db):
    api = client(db)
    named = intake(db)
    ok = api.post('/api/candidates', json={'name': 'Passport Holder', 'passport_no': 'Z1234567', 'intake_group_id': named.group_id})
    assert ok.status_code == 201, ok.text
    nameless = intake(db)
    assert api.post('/api/candidates', json={'passport_no': 'Z7654321', 'intake_group_id': nameless.group_id}).status_code == 422
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(m.Candidate)) == 1


def test_manual_entry_without_a_document_still_needs_name_and_phone(db):
    api = client(db)
    assert api.post('/api/candidates', json={'name': 'Only a name'}).status_code == 422
    assert api.post('/api/candidates', json={'phone': '9000000020'}).status_code == 422
    assert api.post('/api/candidates', json={'name': 'Both', 'phone': '9000000020'}).status_code == 201
    # Editing may not wipe a name or number the candidate already has.
    saved = db.scalar(select(m.Candidate))
    assert api.patch(f'/api/candidates/{saved.id}', json={'name': saved.name, 'phone': ''}).status_code == 422


def test_live_duplicate_check_accepts_an_email_alone(db):
    api = client(db)
    api.post('/api/candidates', json={'name': 'Existing', 'phone': '9000000020', 'email': 'same@example.com'})
    found = api.post('/api/candidates/check-duplicate', json={'email': 'same@example.com'})
    assert found.status_code == 200 and found.json()['duplicate'] is True, found.text
    assert api.post('/api/candidates/check-duplicate', json={'email': 'other@example.com'}).json() == {'duplicate': False}


def test_intake_owner_and_saved_candidate_permissions_are_checked(db):
    job = intake(db)
    api = client(db, 'b')
    body = {'name': 'Candidate', 'phone': '9000000020', 'intake_group_id': job.group_id}
    assert api.post('/api/candidates', json=body).status_code == 404
    saved = client(db).post('/api/candidates', json={'name': 'Another owner', 'phone': '9000000021'}).json()
    own_job = intake(db, owner='b')
    path = f'/api/ocr/jobs/groups/{own_job.group_id}/draft'
    assert api.put(path, json={'savedCandidateId': saved['id']}).status_code == 403
    assert api.put(path, json={'savedCandidateId': 'missing-candidate'}).status_code == 404
    # Also reject a legacy/corrupt stored binding to an unrelated candidate.
    own_job.draft = {'savedCandidateId': saved['id']}; db.commit()
    assert api.post('/api/candidates', json={**body, 'intake_group_id': own_job.group_id}).status_code == 403


def test_staff_who_adds_a_candidate_owns_it_and_can_resume_after_a_lost_response(db):
    job = intake(db, owner='b')
    api = client(db, 'b')
    body = {'name': 'Entered by B', 'phone': '9000000020', 'intake_group_id': job.group_id}
    first = api.post('/api/candidates', json=body)
    assert first.status_code == 201, first.text
    assert first.json()['ownerId'] == 'b'
    assert api.post('/api/candidates', json=body).json()['id'] == first.json()['id']
    # An admin gives the candidate to A; B may still read the saved result back, but it is no longer B's to work.
    db.get(m.Candidate, first.json()['id']).owner_id = 'a'; db.commit()
    assert api.post('/api/candidates', json=body).json()['id'] == first.json()['id']
    assert api.post(f"/api/ocr/jobs/{job.id}/attach", json={'candidateId': first.json()['id']}).status_code == 403


def test_merge_is_recoverable_without_reapplying_evidence_or_resetting_work(db):
    api = client(db)
    original = api.post('/api/candidates', json={'name': 'Original', 'phone': '9000000020', 'ocr_data': {'resume': {'name': 'Original evidence'}}}).json()
    task = db.get(m.Task, original['taskId'])
    due, owner = task.due_at, task.owner_id
    job = intake(db)
    payload = {'name': 'Incoming', 'phone': '9000000020', 'email': 'new@example.test',
        'intake_group_id': job.group_id, 'ocr_data': {'resume': {'name': 'New evidence'}}}
    for _ in range(2):
        response = api.post(f"/api/candidates/{original['id']}/merge", json=payload)
        assert response.status_code == 200, response.text
    db.expire_all()
    candidate = db.get(m.Candidate, original['id'])
    assert candidate.name == 'Original' and candidate.email == 'new@example.test'
    assert len(candidate.ocr_data['additional_documents']) == 1
    assert db.get(m.DocumentExtractionJob, job.id).draft['savedCandidateId'] == original['id']
    assert db.get(m.Task, original['taskId']).due_at == due
    assert db.get(m.Task, original['taskId']).owner_id == owner
    assert db.scalar(select(func.count()).select_from(m.AuditEntry).where(m.AuditEntry.action == 'Merged candidate entry')) == 1


def test_failed_merge_does_not_bind_and_bound_group_cannot_move_to_another_candidate(db):
    api = client(db)
    first = api.post('/api/candidates', json={'name': 'First', 'phone': '9000000020'}).json()
    second = api.post('/api/candidates', json={'name': 'Second', 'phone': '9000000021'}).json()
    job = intake(db)
    body = {'name': 'First', 'phone': '9000000020', 'intake_group_id': job.group_id}
    assert api.post(f"/api/candidates/{second['id']}/merge", json=body).status_code == 409
    db.rollback(); db.expire_all()
    assert not db.get(m.DocumentExtractionJob, job.id).draft.get('savedCandidateId')
    assert api.post(f"/api/candidates/{first['id']}/merge", json=body).status_code == 200
    assert api.post(f"/api/candidates/{second['id']}/merge", json={**body, 'phone': '9000000021'}).status_code == 409


def test_non_owner_can_neither_combine_an_upload_nor_attach_its_files(db):
    original = client(db).post('/api/candidates', json={'name': 'Original', 'phone': '9000000020', 'assign_to': 'a'}).json()
    job = intake(db, owner='b')
    other = client(db, 'b')
    assert db.get(m.Candidate, original['id']).owner_id == 'a'
    attach = f"/api/ocr/jobs/{job.id}/attach"
    assert other.post(attach, json={'candidateId': original['id']}).status_code == 403
    merged = other.post(f"/api/candidates/{original['id']}/merge", json={'name': 'Incoming', 'phone': '9000000020', 'intake_group_id': job.group_id})
    assert merged.status_code == 403, merged.text
    assert other.post(attach, json={'candidateId': original['id']}).status_code == 403
    db.expire_all()
    assert 'savedCandidateId' not in (db.get(m.DocumentExtractionJob, job.id).draft or {})
    # The owner can: the synthetic job has no stored bytes, so this fails on storage (503), not on permission.
    owner_job = intake(db, owner='a')
    assert client(db, 'a').post(f"/api/ocr/jobs/{owner_job.id}/attach", json={'candidateId': original['id']}).status_code == 503
