"""Data Bank search: case-insensitive, one box over every detail, comma-separated terms all have to match,
and the position groups agree with the list. Real PostgreSQL (isolated test database only)."""
from datetime import date, datetime, timezone
from io import BytesIO
import json
from xml.etree import ElementTree
from zipfile import ZipFile
import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.db import models as m
from app.services.candidate_search import Search, export_candidates, pool_counts, position_groups, search_candidates, search_terms
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


def test_default_new_candidates_first_orders_the_entire_result_before_paging(bank):
    # Every non-New record is newer than every New record, so sorting a fetched
    # newest-first page in memory could never produce the expected first pages.
    records = [
        ('new-old', m.CandidateStatus.new, 1),
        ('new-b', m.CandidateStatus.new, 2),
        ('new-a', m.CandidateStatus.new, 2),
        ('process-old', m.CandidateStatus.in_process, 3),
        ('process-new', m.CandidateStatus.in_process, 4),
        ('deployed', m.CandidateStatus.deployed, 5),
        ('review', m.CandidateStatus.needs_review, 6),
    ]
    for identifier, status, day in records:
        add(bank, identifier, status=status, created_at=datetime(2026, 1, day, tzinfo=timezone.utc))
    bank.commit()
    pages = [search_candidates(bank, Search(), page, 2) for page in range(4)]
    assert all(page['total'] == 7 for page in pages)
    assert [[row['id'] for row in page['items']] for page in pages] == [
        ['new-a', 'new-b'], ['new-old', 'process-new'], ['process-old', 'review'], ['deployed'],
    ]
    explicit = search_candidates(bank, Search(sort='added', direction='desc'), 0, 2)
    assert [row['id'] for row in explicit['items']] == ['review', 'deployed']
    oldest = search_candidates(bank, Search(sort='added', direction='asc'), 0, 2)
    assert [row['id'] for row in oldest['items']] == ['new-old', 'new-a']
    alphabetical = search_candidates(bank, Search(sort='name', direction='asc'), 0, 2)
    assert [row['id'] for row in alphabetical['items']] == ['deployed', 'new-a']
    client = make_client(bank, 'asha')
    default_page = client.get('/api/candidates', params={'page': 1, 'page_size': 2})
    assert default_page.status_code == 200, default_page.text
    assert [row['id'] for row in default_page.json()['items']] == ['new-old', 'process-new']
    newest_page = client.get('/api/candidates', params={'sort': 'added', 'direction': 'desc', 'page_size': 2})
    assert newest_page.status_code == 200, newest_page.text
    assert [row['id'] for row in newest_page.json()['items']] == ['review', 'deployed']


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


def test_review_filter_includes_flagged_pipeline_statuses_without_replacing_them(bank):
    add(bank, 'flagged', status=m.CandidateStatus.deployed, needs_review=True)
    add(bank, 'complete', status=m.CandidateStatus.deployed, needs_review=False)
    add(bank, 'review', status=m.CandidateStatus.needs_review, needs_review=False)
    add(bank, 'new', status=m.CandidateStatus.new, needs_review=False)
    bank.commit()
    expected = {'flagged', 'review'}
    for label in ('Needs review', 'needs_review', 'needs', 'review'):
        assert ids(bank, '', column_filters=json.dumps({'status': label})) == expected
    assert ids(bank, '', pool='needs_review') == expected
    assert ids(bank, 'needs review') == expected
    assert pool_counts(bank)['needs_review'] == 2
    assert ids(bank, '', column_filters='{"status":"Deployed"}') == {'flagged', 'complete'}
    assert ids(bank, '', pool='needs_review', column_filters='{"status":"deployed"}') == {'flagged'}
    rows = search_candidates(bank, Search(sort='status', direction='asc'), 0, 50)['items']
    assert len(rows) == 4 and next(row for row in rows if row['id'] == 'flagged')['status'] == 'deployed'


def test_deployed_candidates_remain_in_their_order_stage_and_counts_even_when_flagged(bank):
    for index in range(26):
        identifier = f'travelled-{index:02d}'
        add(bank, identifier, status=m.CandidateStatus.deployed, needs_review=index < 7)
        bank.add(m.Application(id=f'placement-{index}', candidate_id=identifier, order_id='o-1', owner_id='asha',
                               current_stage=11, status=m.ApplicationStatus.deployed))
    add(bank, 'unplaced-1')
    add(bank, 'unplaced-2')
    bank.commit()
    deployed = {f'travelled-{index:02d}' for index in range(26)}
    assert ids(bank, '', column_filters='{"status":"deployed"}') == deployed
    assert ids(bank, '', column_filters='{"stage":"11"}') == deployed
    assert ids(bank, '', column_filters='{"order":"ORD-WELD"}') == deployed
    assert ids(bank, '', order_id='o-1') == deployed
    assert ids(bank, 'al noor, welder') == deployed
    assert ids(bank, '', order_id='none') == {'unplaced-1', 'unplaced-2'}
    assert ids(bank, '', pool='unmapped') == {'unplaced-1', 'unplaced-2'}
    counts = pool_counts(bank)
    assert (counts['all'], counts['unmapped'], counts['needs_review']) == (28, 2, 7)
    groups = position_groups(bank, Search())
    assert groups['positions'] == 1 and groups['resumes'] == 28
    assert {(row['orderId'], row['resumes']) for row in groups['groups']} == {('o-1', 26), (None, 2)}


def test_displayed_placement_prefers_active_then_latest_deployed_and_never_duplicates_rows(bank):
    for identifier, state in [('history', m.CandidateStatus.deployed), ('active', m.CandidateStatus.in_process),
                              ('closed', m.CandidateStatus.new), ('unplaced', m.CandidateStatus.new)]:
        add(bank, identifier, status=state)

    def placement(identifier, candidate, order, state, year, stage):
        bank.add(m.Application(id=identifier, candidate_id=candidate, order_id=order, owner_id='asha', current_stage=stage,
                               status=state, created_at=datetime(year, 1, 1, tzinfo=timezone.utc)))

    placement('history-old', 'history', 'o-1', m.ApplicationStatus.deployed, 2024, 11)
    placement('history-new', 'history', 'o-2', m.ApplicationStatus.deployed, 2025, 11)
    placement('history-closed', 'history', 'o-1', m.ApplicationStatus.closed, 2026, 2)
    placement('active-current', 'active', 'o-1', m.ApplicationStatus.active, 2023, 4)
    placement('active-travelled', 'active', 'o-2', m.ApplicationStatus.deployed, 2025, 11)
    placement('closed-only', 'closed', 'o-2', m.ApplicationStatus.closed, 2026, 1)
    bank.commit()
    assert ids(bank, '', order_id='o-1') == {'active'}
    assert ids(bank, '', order_id='o-2') == {'history'}
    assert ids(bank, '', column_filters='{"stage":"11"}') == {'history'}
    assert ids(bank, '', column_filters='{"stage":"4"}') == {'active'}
    assert ids(bank, '', order_id='none') == {'closed', 'unplaced'}
    assert ids(bank, '', matchable=True) == {'closed', 'unplaced'}
    page = search_candidates(bank, Search(), 0, 50)
    assert page['total'] == len(page['items']) == 4
    assert position_groups(bank, Search())['resumes'] == 4
    with ZipFile(BytesIO(export_candidates(bank, Search()))) as archive:
        sheet = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    rows = [[''.join(cell.itertext()) for cell in row.findall('{*}c')] for row in sheet.findall('{*}sheetData/{*}row')]
    header = rows[0]
    body = {row[header.index('Name')]: row for row in rows[1:]}
    assert len(body) == 4
    for identifier, order, stage in [('history', 'ORD-ELEC', '11'), ('active', 'ORD-WELD', '4'), ('closed', '', ''), ('unplaced', '', '')]:
        assert body[identifier][header.index('Mapped order')] == order
        assert body[identifier][header.index('Stage')] == stage
    assert body['history'][header.index('Status')] == 'deployed'


def test_agency_search_matches_form_assignment_without_treating_import_provenance_as_an_agency(bank):
    add(bank, 'imported', source=m.Source.other, source_detail='  BULK   import  ')
    add(bank, 'email-agency', source=m.Source.email, source_detail='True Referrer')
    add(bank, 'explicit-referral', source=m.Source.referral, source_detail='Bulk import')
    add(bank, 'lead', source=m.Source.other, source_detail='lead_sheet:private-row-pointer')
    bank.commit()
    assert ids(bank, 'bulk import') == {'explicit-referral'}
    assert ids(bank, 'true referrer') == {'email-agency'}
    assert ids(bank, 'truereferrer') == {'email-agency'}
    assert ids(bank, 'private-row-pointer') == set()
    assert ids(bank, 'lead_sheet') == set()
    bank.get(m.Candidate, 'imported').source_detail = 'Bulk import'
    bank.add(m.ReferenceAgency(id='registered-import', name='Agency Contact', agency_name='BULK IMPORT'))
    bank.commit()
    assert ids(bank, 'bulk import') == {'imported', 'explicit-referral'}
    assert bank.get(m.Candidate, 'lead').source_detail == 'lead_sheet:private-row-pointer'


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
