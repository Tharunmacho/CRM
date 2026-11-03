"""Data Bank search: case-insensitive, one box over every detail, comma-separated terms all have to match,
and the position groups agree with the list. Real PostgreSQL (isolated test database only)."""
from datetime import date
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.db import models as m
from app.services.candidate_search import Search, position_groups, search_candidates, search_terms
from test_candidate_workflows import make_client
from test_database_naming import schema_database, _migrate  # noqa: F401  (fixture)


@pytest.fixture
def bank(schema_database):
    _migrate(schema_database, 'head')
    with Session(schema_database, expire_on_commit=False) as db:
        db.add_all([m.User(id='admin', name='Admin', email='admin@example.test', role=m.Role.admin),
                    m.User(id='asha', name='Asha Menon', email='asha@example.test', role=m.Role.staff),
                    m.User(id='bala', name='Balakrishnan', email='bala@example.test', role=m.Role.staff)])
        db.add_all([m.Client(id='client-1', name='Gulf Hiring Group'), m.Client(id='client-2', name='Doha Builders')])
        db.flush()
        db.add_all([m.Company(id='co-1', client_id='client-1', name='Al Noor Contracting', country='UAE', city='Dubai'),
                    m.Company(id='co-2', client_id='client-2', name='Qatar Steelworks', country='Qatar', city='Doha')])
        db.add_all([m.Designation(id='d-weld', title='Welder'), m.Designation(id='d-elec', title='Electrician')])
        db.flush()
        db.add_all([m.Order(id='o-1', ref='ORD-WELD', company_id='co-1', designation_id='d-weld', country='UAE', openings=5, deadline=date(2030, 1, 1)),
                    m.Order(id='o-2', ref='ORD-ELEC', company_id='co-2', designation_id='d-elec', country='Qatar', openings=3, deadline=date(2030, 1, 1))])
        db.flush()
        yield db


def add(db, identifier, **values):
    body = dict(id=identifier, ref=identifier.upper(), name=identifier, phone='9000000001', source='walk_in', owner_id='asha', skills=[], designations=[])
    body.update(values)
    candidate = m.Candidate(**body)
    db.add(candidate)
    db.flush()
    return candidate


def map_to(db, candidate_id, order_id, stage=1):
    db.add(m.Application(id=f'app-{candidate_id}', candidate_id=candidate_id, order_id=order_id, owner_id='asha', current_stage=stage, status=m.ApplicationStatus.active))
    db.flush()


def ids(db, q, **kwargs):
    return {row['id'] for row in search_candidates(db, Search(q=q, **kwargs), 0, 50)['items']}


@pytest.fixture
def people(bank):
    # Two Ravis with different agents: the name alone cannot tell them apart.
    add(bank, 'ravi-a', name='Ravi Kumar', phone='+91 98765 43210', source='referral', source_detail='Suresh Agent', designations=['Welder'], email='ravi.a@example.test')
    add(bank, 'ravi-b', name='RAVI Shankar', phone='9111111111', source='referral', source_detail='Kumar Agencies', owner_id='bala')
    add(bank, 'meena', name='Meena Das', phone='9222222222', source='email', source_detail='email_message:abc-123', owner_id='bala', skills=['TIG welding'])
    add(bank, 'nobody', name='Nobody Mapped', phone='9333333333', owner_id=None)
    map_to(bank, 'ravi-a', 'o-1', stage=3)
    map_to(bank, 'ravi-b', 'o-2', stage=1)
    map_to(bank, 'meena', 'o-1', stage=1)
    bank.commit()
    return bank


def test_terms_are_split_on_commas_trimmed_and_deduplicated():
    assert search_terms(' Ravi ,, agent   kumar , RAVI,') == ['Ravi', 'agent kumar']
    assert search_terms('') == [] and search_terms(' , ') == []
    with pytest.raises(HTTPException):
        search_terms(','.join(str(n) for n in range(11)))


def test_search_ignores_case_in_every_field(people):
    assert ids(people, 'RAVI') == {'ravi-a', 'ravi-b'}
    assert ids(people, 'ravi kumar') == {'ravi-a'}
    assert ids(people, 'sUrEsH aGeNt') == {'ravi-a'}          # agent / referrer
    assert ids(people, 'BALAKRISHNAN') == {'ravi-b', 'meena'}  # recruiter name
    assert ids(people, 'welder') == {'ravi-a', 'meena'}      # job title on the candidate or on the position they are mapped to
    assert ids(people, 'al noor') == {'ravi-a', 'meena'}      # company
    assert ids(people, 'doha builders') == {'ravi-b'}         # client
    assert ids(people, 'qatar') == {'ravi-b'}                 # country of the position
    assert ids(people, 'ord-elec') == {'ravi-b'}              # order reference
    assert ids(people, 'RAVI.A@EXAMPLE') == {'ravi-a'}
    assert ids(people, 'tig') == {'meena'}                    # skills
    assert ids(people, 'referral') == {'ravi-a', 'ravi-b'}    # source label


def test_stage_status_and_phone_digits_match(people):
    from app.domain.stages import STAGES
    assert ids(people, 'stage 3') == {'ravi-a'}
    title = STAGES[2].title
    assert 'ravi-a' in ids(people, title.upper())
    assert ids(people, 'new') >= {'meena'}                    # status label
    assert ids(people, '98765 43210') == {'ravi-a'}           # a phone typed with spaces
    assert ids(people, '+919876543210') == {'ravi-a'}


def test_machine_pointers_are_not_an_agent_name(people):
    assert ids(people, 'email_message') == set()
    assert ids(people, 'abc-123') == set()


def test_comma_separated_terms_must_all_match_the_same_candidate(people):
    assert ids(people, 'ravi, agent') == {'ravi-a'}
    assert ids(people, 'ravi, suresh') == {'ravi-a'}
    assert ids(people, 'RAVI,KUMAR AGENCIES') == {'ravi-b'}
    assert ids(people, 'ravi, kumar') == {'ravi-a', 'ravi-b'}  # "kumar" is in one name and the other's agent
    assert ids(people, 'ravi, meena') == set()
    assert ids(people, 'ravi, sureshagent') == {'ravi-a'}      # spaces do not have to be typed
    assert ids(people, 'RAVIKUMAR') == {'ravi-a'}
    assert ids(people, 'ravi , , welder , al noor') == {'ravi-a'}
    assert ids(people, 'ravi,') == ids(people, 'ravi')
    # pools still narrow the comma search
    assert ids(people, 'nobody, mapped', pool='unassigned') == {'nobody'}
    assert ids(people, 'ravi', pool='unassigned') == set()


def test_search_pages_and_counts_stay_correct(people):
    page = search_candidates(people, Search(q='ravi, kumar'), 0, 1)
    assert page['total'] == 2 and len(page['items']) == 1
    assert search_candidates(people, Search(q='ravi, kumar'), 1, 1)['items'][0]['id'] != page['items'][0]['id']


def test_position_groups_follow_the_search_and_list_the_unmapped(people):
    everything = position_groups(people, Search())
    assert everything['positions'] == 2 and everything['resumes'] == 4
    weld = next(group for group in everything['groups'] if group['orderId'] == 'o-1')
    assert (weld['title'], weld['company'], weld['client'], weld['country'], weld['resumes'], weld['ref']) == ('Welder', 'Al Noor Contracting', 'Gulf Hiring Group', 'UAE', 2, 'ORD-WELD')
    assert everything['groups'][-1]['orderId'] is None and everything['groups'][-1]['resumes'] == 1   # "No position yet" is last
    narrowed = position_groups(people, Search(q='ravi, kumar'))
    assert [(group['orderId'], group['resumes']) for group in narrowed['groups']] == [('o-2', 1), ('o-1', 1)]
    assert narrowed['resumes'] == 2
    only = position_groups(people, Search(q='ravi, suresh'))
    assert [(group['orderId'], group['resumes']) for group in only['groups']] == [('o-1', 1)]


def test_a_position_lists_only_its_own_resumes(people):
    assert ids(people, '', order_id='o-1') == {'ravi-a', 'meena'}
    assert ids(people, 'ravi', order_id='o-1') == {'ravi-a'}
    assert ids(people, '', order_id='none') == {'nobody'}


def test_endpoints_serve_the_search_and_groups_without_breaking_order_matching(people):
    client = make_client(people, 'asha')
    listing = client.get('/api/candidates', params={'q': 'Ravi, AGENCIES', 'position_id': 'o-2'})
    assert listing.status_code == 200, listing.text
    assert [row['id'] for row in listing.json()['items']] == ['ravi-b']
    groups = client.get('/api/candidates/position-groups', params={'q': 'ravi, suresh'})
    assert groups.status_code == 200, groups.text
    assert groups.headers['cache-control'] == 'private, no-store'
    assert groups.json()['groups'][0]['title'] == 'Welder'
    # /matching also takes order_id: the new position filter must not leak into it.
    matching = client.get('/api/candidates/matching', params={'order_id': 'o-1'})
    assert matching.status_code == 200, matching.text
    assert client.get('/api/candidates', params={'q': 'a' * 501}).status_code == 422
