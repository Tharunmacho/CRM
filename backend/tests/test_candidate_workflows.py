"""Manual identity, correction, bounded search and export on a guarded test database."""
from datetime import date, datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routers import candidates as route
from app.core.deps import current_user
from app.db.session import get_db
from app.db import models as m
from app.services.candidate_identity import normalize_phone, enrich_empty, identity_matches
from app.services.candidate_search import Search, search_candidates
from test_database_naming import schema_database, _migrate


@pytest.fixture
def db(schema_database):
    _migrate(schema_database, 'head')
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id='admin', name='Admin', email='admin@example.test', role=m.Role.admin),
                    m.User(id='a', name='A', email='a@example.test', role=m.Role.staff, receives_auto_assign=True),
                    m.User(id='b', name='B', email='b@example.test', role=m.Role.staff, receives_auto_assign=True)])
        db.commit()
        yield db


def make_client(db, who='admin'):
    app = FastAPI(); app.include_router(route.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[current_user] = lambda: db.get(m.User, who)
    return TestClient(app)


def create(db, **values):
    return route.create_candidate(route.CandidateIn(name='Synthetic candidate', phone='9000000001', **values), db, db.get(m.User, 'admin'))


def test_phone_normalization_does_not_collapse_foreign_country_codes():
    assert normalize_phone('+91 90000 00001') == normalize_phone('0091-9000000001') == normalize_phone('09000000001') == '9000000001'
    assert normalize_phone('+44 90000 00001') != normalize_phone('+91 90000 00001')
    assert normalize_phone('12') == ''


def test_empty_enrichment_preserves_human_zero_and_original_evidence():
    candidate = SimpleNamespace(name='Reviewed human name', total_experience=0, email=None, skills=[], ocr_data={'resume': {'name': 'First'}})
    changed = enrich_empty(candidate, {'name': 'OCR name', 'total_experience': 9, 'email': ' TEST@EXAMPLE.TEST ', 'skills': [' Welding ', 'welding']}, {'resume': {'name': 'Second'}})
    assert candidate.name == 'Reviewed human name' and candidate.total_experience == 0
    assert changed == ['email', 'skills']
    assert candidate.skills == ['Welding'] and candidate.email == 'test@example.test'
    assert candidate.ocr_data == {'resume': {'name': 'First'}, 'additional_documents': [{'resume': {'name': 'Second'}}]}


def test_manual_assignment_snapshot_due_time_notifications_and_sequence(db):
    response = create(db)
    candidate = db.get(m.Candidate, response.id)
    task = db.get(m.Task, response.task_id)
    assert candidate.owner_id == candidate.intake_owner_id == 'a'
    assert candidate.created_by == 'admin'
    assert (task.due_at - task.sla_started_at).total_seconds() == 48 * 3600
    assert task.owner_id == 'a'
    assert candidate.ref.startswith('CND-')
    assert set(db.scalars(select(m.Notification.user_id))) == {'a', 'admin'}


def test_no_eligible_manual_candidate_is_persisted_for_review(db):
    for user in db.scalars(select(m.User).where(m.User.role == 'staff')):
        user.on_leave = True
    db.commit()
    created = create(db)
    candidate = db.get(m.Candidate, created.id)
    assert candidate.needs_review and candidate.status == m.CandidateStatus.needs_review
    assert candidate.owner_id is None and created.task_id is None
    assert db.scalar(select(func.count()).select_from(m.Task)) == 0
    assert db.scalar(select(m.Notification.title)) == 'Candidate needs assignment'


@pytest.mark.parametrize('fields', [{'phone': '+91 90000 00001'}, {'phone': '9000000009', 'email': ' PERSON@EXAMPLE.TEST '}, {'phone': '9000000009', 'passport_no': ' p 123 4567 '}])
def test_duplicate_or_identity_without_date_of_birth(db, fields):
    original = create(db, email='person@example.test', passport_no='P1234567')
    response = make_client(db).post('/api/candidates', json={'name': 'Duplicate', **fields})
    assert response.status_code == 409
    assert response.json()['detail']['candidate']['id'] == original.id
    assert db.scalar(select(func.count()).select_from(m.Candidate)) == 1


def test_manual_creation_persists_phone_normalization_used_by_duplicate_lookup(db):
    first = make_client(db).post('/api/candidates', json={
        'name': 'Synthetic candidate', 'phone': '\uff19\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff11',
        'whatsapp': '\uff19\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff10\uff12',
    })
    assert first.status_code == 201, first.text
    candidate = db.get(m.Candidate, first.json()['id'])
    assert candidate.phone == '9000000001' and candidate.whatsapp == '9000000002'
    duplicate = make_client(db).post('/api/candidates', json={'name': 'Same number', 'phone': '9000000001'})
    assert duplicate.status_code == 409, duplicate.text
    assert db.scalar(select(func.count()).select_from(m.Candidate)) == 1


def test_explicit_merge_fills_empty_fields_and_preserves_workflow(db):
    original = create(db)
    candidate = db.get(m.Candidate, original.id)
    candidate.skills = ['Human skill']; candidate.total_experience = 0
    db.commit()
    client = make_client(db, 'a')
    merged = client.post(f'/api/candidates/{original.id}/merge', json={'name': 'Other OCR name', 'phone': '+919000000001', 'email': 'new@example.test', 'total_experience': 9, 'skills': ['New skill']})
    assert merged.status_code == 200
    db.refresh(candidate)
    assert candidate.name == 'Synthetic candidate' and candidate.skills == ['Human skill'] and candidate.total_experience == 0
    assert candidate.email == 'new@example.test' and candidate.owner_id == 'a'
    assert db.scalar(select(func.count()).select_from(m.Task)) == 1
    assert make_client(db, 'b').post(f'/api/candidates/{original.id}/merge', json={'name': 'No', 'phone': '9000000001'}).status_code == 403


def test_merge_validates_new_dates_together_with_retained_profile(db):
    original = create(db, passport_expiry=date(2030, 1, 1))
    response = make_client(db).post(f'/api/candidates/{original.id}/merge', json={
        'name': 'Synthetic candidate', 'phone': '9000000001',
        'passport_issue': '2031-01-01', 'passport_expiry': '2041-01-01', 'email': 'must-not-save@example.test',
        'ocr_data': {'passport': {'synthetic': True}},
    })
    assert response.status_code == 409, response.text
    db.expire_all()
    candidate = db.get(m.Candidate, original.id)
    assert candidate.passport_issue is None and candidate.passport_expiry == date(2030, 1, 1)
    assert candidate.email is None and not candidate.ocr_data
    assert not db.scalar(select(m.AuditEntry.id).where(m.AuditEntry.action == 'Merged candidate entry'))


def test_ambiguous_identity_never_merges_arbitrary_candidate(db):
    first = create(db)
    db.add(m.Candidate(id='other', ref='OTHER', name='Other', phone='9000000002', email='other@example.test', source='walk_in'))
    db.commit()
    body = {'name': 'Ambiguous', 'phone': '9000000001', 'email': 'other@example.test'}
    assert make_client(db).post('/api/candidates', json=body).status_code == 409
    assert make_client(db).post(f'/api/candidates/{first.id}/merge', json=body).status_code == 409


def test_correction_role_review_and_identity_guards_persist(db):
    original = create(db)
    candidate = db.get(m.Candidate, original.id); candidate.needs_review = True; candidate.status = m.CandidateStatus.needs_review; db.commit()
    body = {'name': 'Corrected name', 'phone': '9000000001', 'skills': ['TIG Welding', ' tig   welding '], 'mark_reviewed': True}
    assert make_client(db, 'b').patch(f'/api/candidates/{original.id}', json=body).status_code == 403
    assert make_client(db, 'a').patch(f'/api/candidates/{original.id}', json=body).status_code == 200
    db.refresh(candidate)
    assert candidate.name == 'Corrected name' and candidate.skills == ['TIG Welding']
    assert not candidate.needs_review and candidate.status == m.CandidateStatus.new
    assert candidate.owner_id == 'a' and candidate.intake_owner_id == 'a'
    audit = db.scalar(select(m.AuditEntry).where(m.AuditEntry.action == 'Corrected candidate'))
    assert audit.meta['review_completed']


@pytest.mark.parametrize('changes', [
    {'passport_issue': '2031-01-01'},
    {'passport_expiry': '2019-01-01'},
    {'total_experience': 1},
])
def test_partial_correction_validates_against_retained_values(db, changes):
    original = create(db, passport_issue=date(2020, 1, 1), passport_expiry=date(2030, 1, 1),
                      total_experience=8, gulf_experience=3)
    response = make_client(db).patch(f'/api/candidates/{original.id}', json={
        'name': 'Must not persist', 'phone': '9000000001', **changes,
    })
    assert response.status_code == 422, response.text
    db.expire_all()
    candidate = db.get(m.Candidate, original.id)
    assert candidate.name == 'Synthetic candidate'
    assert candidate.passport_issue == date(2020, 1, 1)
    assert candidate.passport_expiry == date(2030, 1, 1)
    assert (candidate.total_experience, candidate.gulf_experience) == (8, 3)
    assert not db.scalar(select(m.AuditEntry.id).where(m.AuditEntry.action == 'Corrected candidate'))


def test_partial_correction_can_change_gulf_experience_without_resending_total(db):
    original = create(db, total_experience=8, gulf_experience=3)
    response = make_client(db).patch(f'/api/candidates/{original.id}', json={
        'name': 'Synthetic candidate', 'phone': '9000000001', 'gulf_experience': 5,
    })
    assert response.status_code == 200, response.text
    db.expire_all()
    candidate = db.get(m.Candidate, original.id)
    assert (candidate.total_experience, candidate.gulf_experience) == (8, 5)


def test_bounded_search_exact_skills_no_skills_and_fulltext(db):
    for index, skills in enumerate([['ＣＮＣ', ' Welding '], ['Welding'], [], ['Plumbing']]):
        db.add(m.Candidate(id=f'c{index}', ref=f'REF-{index}', name=f'Candidate {index}', phone=f'900000001{index}', source='walk_in',
                           skills=skills, resume_text='Hydraulic maintenance specialist' if index == 3 else '', owner_id='b'))
    db.commit()
    result = search_candidates(db, Search(skills=['cnc', 'welding']), 0, 2)
    assert [r['id'] for r in result['items']] == ['c0']
    assert search_candidates(db, Search(skills=['CNC', 'welding'], skill_mode='any'), 0, 2)['total'] == 2
    assert search_candidates(db, Search(missing_skills=True), 0, 2)['items'][0]['id'] == 'c2'
    assert search_candidates(db, Search(q='hydraulic specialist'), 0, 2)['items'][0]['id'] == 'c3'
    response = make_client(db, 'a').get('/api/candidates?page_size=2&page=1&sort=name&direction=asc')
    assert response.status_code == 200 and response.json()['total'] == 4
    assert [r['id'] for r in response.json()['items']] == ['c2', 'c3']  # Staff sees every candidate.
    assert make_client(db).get('/api/candidates?page_size=101').status_code == 422


def test_column_filters_export_parity_and_admin_guard(db):
    for index in range(3):
        db.add(m.Candidate(ref=f'EX-{index}', name='=FORMULA' if index == 0 else f'Name {index}', phone=f'900000001{index}', source='email',
                           skills=['Welding'] if index < 2 else [], total_experience=4, owner_id='b'))
    db.commit()
    url = '/api/candidates?skills=welding&column_filters={"experience":"4","source":"Email"}&page_size=1'
    listing = make_client(db, 'a').get(url)
    assert listing.status_code == 200 and listing.json()['total'] == 2
    assert make_client(db, 'a').get('/api/candidates/export').status_code == 403
    response = make_client(db).get(url.replace('/api/candidates?', '/api/candidates/export?'))
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as workbook:
        xml = workbook.read('xl/worksheets/sheet1.xml').decode()
        assert '=FORMULA' in xml and '<f>' not in xml
        assert 'Name 1' in xml and 'Name 2' not in xml
        assert xml.count('<row ') == 3


@pytest.mark.parametrize('body', [
    {'name': ' ', 'phone': '9000000001'}, {'name': 'Name', 'phone': '123'},
    {'name': 'Name', 'phone': '9000000001', 'total_experience': -1},
    {'name': 'Name', 'phone': '9000000001', 'total_experience': 1, 'gulf_experience': 2},
])
def test_manual_validation_rejects_invalid_inputs(body):
    with pytest.raises(ValueError):
        route.CandidateIn(**body)
