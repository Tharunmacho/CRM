"""Candidate saves and document recovery share one transaction and one identity."""
from uuid import uuid4

from sqlalchemy import func, select

from app.api.routers import document_jobs
from app.db import models as m
from test_candidate_workflows import db, make_client
from test_database_naming import schema_database


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


def test_creator_can_resume_after_automatic_assignment_to_another_recruiter(db):
    job = intake(db, owner='b')
    api = client(db, 'b')
    body = {'name': 'Entered by B', 'phone': '9000000020', 'intake_group_id': job.group_id}
    first = api.post('/api/candidates', json=body)
    assert first.status_code == 201, first.text
    assert first.json()['ownerId'] == 'a'
    assert api.post('/api/candidates', json=body).json()['id'] == first.json()['id']


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
