"""Saved skill groups and bounded order matching use real PostgreSQL evidence."""
from datetime import date
import pytest
from sqlalchemy.orm import Session
from app.db import models as m
from app.services.candidate_search import Search, candidate_skill_facets, search_candidates
from test_candidate_workflows import make_client
from test_database_naming import schema_database, _migrate


@pytest.fixture
def desk(schema_database):
    _migrate(schema_database, 'head')
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id='admin', name='Admin', email='admin@example.test', role=m.Role.admin),
                    m.User(id='a', name='A', email='a@example.test', role=m.Role.staff),
                    m.User(id='b', name='B', email='b@example.test', role=m.Role.staff),
                    m.User(id='inactive', name='Former employee', email='former@example.test', role=m.Role.staff, active=False)])
        db.add(m.Client(id='client', name='Synthetic client'))
        db.flush()
        db.add(m.Company(id='company', client_id='client', name='Synthetic employer', country='Qatar', city='Doha'))
        db.add(m.Designation(id='designation', title='Welder', required_skills=['TIG', 'Blueprint reading'], min_experience=3))
        db.flush()
        db.add(m.Order(id='order', ref='ORDER-1', company_id='company', designation_id='designation', country='Qatar', city='Doha', openings=20, deadline=date(2030, 1, 1)))
        db.flush()
        yield db


def add(db, identifier, **values):
    body = dict(id=identifier, ref=identifier.upper(), name=identifier, phone='9000000001', source='walk_in', owner_id='a', skills=[], designations=['Welder'], total_experience=4, current_city='Chennai')
    body.update(values)
    candidate = m.Candidate(**body)
    db.add(candidate)
    db.flush()
    return candidate


def test_skill_groups_count_people_once_and_preserve_exact_trade_names(desk):
    add(desk, 'c1', skills=[' ＣＮＣ ', 'cnc', 'TIG', '\tTIG\n', 'JavaScript'])
    add(desk, 'c2', skills=['CNC', ' TIG '], owner_id='b')
    add(desk, 'c3', skills=['', '  ', '\t\n'])
    add(desk, 'c4', skills=[], status=m.CandidateStatus.not_interested)
    desk.commit()
    response = make_client(desk, 'a').get('/api/candidates/skill-facets')
    assert response.status_code == 200, response.text
    data = response.json()
    assert (data['total'], data['recorded'], data['missing']) == (4, 2, 2)
    assert {row['key']: row['count'] for row in data['skills']} == {'cnc': 2, 'tig': 2, 'javascript': 1}
    # Facet counts and exact filtered list include the same people, not substrings.
    for facet in data['skills']:
        listing = make_client(desk, 'a').get('/api/candidates', params={'skills': facet['key']}).json()
        assert listing['total'] == facet['count']
    assert search_candidates(desk, Search(skills=['Java']), 0, 20)['total'] == 0
    scoped = make_client(desk).get('/api/candidates/skill-facets?staff=a&pool=not_interested').json()
    assert scoped == {'skills': [], 'total': 1, 'recorded': 0, 'missing': 1}
    missing = search_candidates(desk, Search(missing_skills=True), 0, 20)
    assert {row['id'] for row in missing['items']} == {'c3', 'c4'}
    assert response.headers['cache-control'] == 'private, no-store'


def test_matching_is_bounded_current_and_respects_application_eligibility(desk):
    for index in range(25):
        add(desk, f'person-{index:02}', skills=['TIG'])
    add(desk, 'mapped', skills=['TIG'])
    add(desk, 'deployed', status=m.CandidateStatus.deployed, skills=['TIG'])
    add(desk, 'unassigned', owner_id=None, skills=['TIG'])
    add(desk, 'inactive-owner', owner_id='inactive', skills=['TIG'])
    add(desk, 'declined', status=m.CandidateStatus.not_interested, skills=['TIG'])
    desk.add(m.Application(id='active', candidate_id='mapped', order_id='order', owner_id='a', status=m.ApplicationStatus.active))
    desk.commit()
    client = make_client(desk, 'b')
    first = client.get('/api/candidates/matching?order_id=order&page_size=20&sort=name&direction=asc')
    assert first.status_code == 200, first.text
    data = first.json()
    assert data['total'] == 26 and len(data['items']) == 20
    second = client.get('/api/candidates/matching?order_id=order&page_size=20&page=1&sort=name&direction=asc').json()
    assert len(second['items']) == 6
    assert not ({row['id'] for row in data['items']} & {row['id'] for row in second['items']})
    assert 'declined' in {row['id'] for row in data['items']}
    assert candidate_skill_facets(desk, Search(matchable=True))['total'] == 26
    # Another session changes ownership: the endpoint re-queries rather than using bootstrap.
    desk.get(m.Candidate, 'person-24').owner_id = None
    desk.commit()
    assert client.get('/api/candidates/matching?order_id=order').json()['total'] == 25
    assert first.headers['cache-control'] == 'private, no-store'


def test_matching_filters_and_explanations_use_saved_evidence(desk):
    add(desk, 'strong', skills=[' ＴＩＧ ', 'Blueprint  reading'], designations=[' Welder '], passport_no='P12345', current_city=' Chennai ', total_experience=5)
    add(desk, 'partial', skills=['TIG'], owner_id='b', total_experience=2, resume_text='Hydraulic maintenance specialist')
    add(desk, 'other', skills=['MIG'], designations=['Mechanical Engineer'])
    desk.commit()
    client = make_client(desk)
    all_rows = client.get('/api/candidates/matching?order_id=order').json()
    assert all_rows['requirements'] == {'title': 'Welder', 'skills': ['TIG', 'Blueprint reading'], 'minExperience': 3, 'orderStatus': 'open'}
    partial = next(row for row in all_rows['items'] if row['id'] == 'partial')
    assert partial['matchEvidence'] == {'matchedSkills': ['TIG'], 'missingSkills': ['Blueprint reading'], 'meetsExperience': False}
    assert partial['resumeText'] == ''
    params = [('order_id', 'order'), ('designation', ' welder '), ('min_experience', '3'), ('passport', 'present'), ('location', 'CHENNAI'), ('staff', 'a'), ('skills', 'tig'), ('skills', 'blueprint reading')]
    result = client.get('/api/candidates/matching', params=params).json()
    assert [row['id'] for row in result['items']] == ['strong']
    assert result['items'][0]['matchEvidence']['missingSkills'] == []
    assert client.get('/api/candidates/matching?order_id=order&skills=tig&skills=blueprint%20reading&skill_mode=any').json()['total'] == 2
    assert client.get('/api/candidates/matching?order_id=order&q=hydraulic%20specialist').json()['items'][0]['id'] == 'partial'
    assert client.get('/api/candidates/matching?order_id=order&skills=welding').json()['total'] == 0


def test_missing_order_and_invalid_filters_are_visible_errors(desk):
    desk.commit()
    client = make_client(desk)
    assert client.get('/api/candidates/matching?order_id=missing').status_code == 404
    for suffix in ('min_experience=-1', 'min_experience=nan', 'skill_mode=guess', 'page_size=101', 'passport=copy'):
        assert client.get('/api/candidates/matching?order_id=order&' + suffix).status_code == 422
