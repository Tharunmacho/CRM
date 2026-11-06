"""The Data Bank grid table's extra fields: the list returns the screening answers, sorts by country, service charges
and interest level (by meaning), searches the preferred country, and the Excel export carries the same columns.
Real PostgreSQL (isolated test database only)."""
from datetime import date
from io import BytesIO
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

from app.db import models as m
from app.services.candidate_search import Search, export_candidates, search_candidates
from test_data_bank_search import add, bank  # noqa: F401  (fixture)
from test_database_naming import schema_database  # noqa: F401  (fixture)


@pytest.fixture
def grid(bank):
    add(bank, 'one', name='Ravi Kumar', whatsapp='+91 98765 43210', dob=date(1994, 3, 12), country_preference='Saudi Arabia, UAE',
        service_charges_status='agreed', interest_level='more_interested', designations=['Welder', 'Fitter'])
    add(bank, 'two', name='Meena Das', country_preference='Qatar', service_charges_status='pending', interest_level='somewhat_interested')
    add(bank, 'three', name='Imran Sheikh', country_preference='Kuwait', service_charges_status='declined', interest_level='not_interested')
    add(bank, 'four', name='Nobody Noted')  # defaults: service charges not discussed, no interest level, no country
    add(bank, 'five', name='Anil Varma', country_preference='Oman', service_charges_status='agreed', interest_level='interested')
    bank.commit()
    return bank


def listing(db, **kwargs):
    return search_candidates(db, Search(**kwargs), 0, 50)['items']


def test_list_rows_carry_the_screening_fields_the_table_shows(grid):
    row = next(item for item in listing(grid) if item['id'] == 'one')
    assert row['countryPreference'] == 'Saudi Arabia, UAE'
    assert row['serviceChargesStatus'] == 'agreed'
    assert row['interestLevel'] == 'more_interested'
    assert row['whatsapp'] == '+91 98765 43210'
    assert row['dob'] == '1994-03-12'
    assert row['designations'] == ['Welder', 'Fitter']
    plain = next(item for item in listing(grid) if item['id'] == 'four')
    assert plain['serviceChargesStatus'] == 'not_discussed' and plain['interestLevel'] is None and plain['countryPreference'] is None


def test_sort_by_service_charges_follows_the_meaning_not_the_alphabet(grid):
    order = [item['id'] for item in listing(grid, sort='service', direction='asc')]
    # agreed (one, five), pending (two), declined (three), not discussed (four)
    assert set(order[:2]) == {'one', 'five'} and order[2:] == ['two', 'three', 'four']


def test_sort_by_interest_level_puts_the_keenest_first_and_unrecorded_last(grid):
    assert [item['id'] for item in listing(grid, sort='interest', direction='asc')] == ['one', 'five', 'two', 'three', 'four']
    assert [item['id'] for item in listing(grid, sort='interest', direction='desc')][0] == 'four'


def test_sort_by_preferred_country_and_search_finds_a_country(grid):
    countries = [item['countryPreference'] for item in listing(grid, sort='country', direction='asc')]
    assert countries[:4] == ['Kuwait', 'Oman', 'Qatar', 'Saudi Arabia, UAE']
    found = {item['id'] for item in listing(grid, q='kuwait')}
    assert found == {'three'}
    assert {item['id'] for item in listing(grid, q='uae')} == {'one'}


def test_unknown_sort_is_still_refused(grid):
    with pytest.raises(HTTPException):
        listing(grid, sort='nonsense')


def test_export_has_the_same_extra_columns_with_the_labels_admins_set(grid):
    with ZipFile(BytesIO(export_candidates(grid, Search(sort='name', direction='asc')))) as archive:
        sheet = ElementTree.fromstring(archive.read('xl/worksheets/sheet1.xml'))
    rows = [[''.join(cell.itertext()) for cell in row.findall('{*}c')] for row in sheet.findall('{*}sheetData/{*}row')]
    header, body = rows[0], {row[1]: row for row in rows[1:]}
    assert header[-5:] == ['Date of birth', 'WhatsApp', 'Preferred country', 'Service charges', 'Interest level']
    assert body['Ravi Kumar'][-5:-1] == ['1994-03-12', '+91 98765 43210', 'Saudi Arabia, UAE', body['Ravi Kumar'][-2]]
    assert body['Ravi Kumar'][-2] in ('Agreed', 'Agreed to pay') and body['Ravi Kumar'][-1] == 'More interested'
    assert body['Nobody Noted'][-2] == 'Not discussed' and body['Nobody Noted'][-1] == ''
