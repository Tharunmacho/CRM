"""Authenticated startup stays private and compact without an extra round trip."""

from sqlalchemy import update

from app.core.security import SESSION_COOKIE
from app.db.models import Candidate, Session as SessionRow
from test_bootstrap_performance import (
    assert_no_private_data, assert_select_budget, desk_client, desk_engine,
    recorded_statements, schema_database,
)


def test_combined_startup_matches_bootstrap_with_two_queries(desk_engine, desk_client):
    expected = desk_client.get('/api/bootstrap').json()
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get('/api/auth/session?workspace=true')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json() == expected
    assert_select_budget(statements, 2)
    assert_no_private_data(response.json())


def test_compact_startup_omits_only_resume_text(desk_engine, desk_client):
    resume = 'Synthetic welding experience. ' * 5000
    with desk_engine.begin() as connection:
        connection.execute(update(Candidate).values(resume_text=resume))
    full = desk_client.get('/api/bootstrap')
    with recorded_statements(desk_engine) as statements:
        compact = desk_client.get('/api/auth/session?workspace=true&compact=true')
    assert compact.status_code == 200
    assert_select_budget(statements, 2)
    expected = full.json()
    assert expected['candidates'][0]['resumeText'] == resume
    for row in expected['candidates']:
        row['resumeText'] = ''
    assert compact.json() == expected
    assert len(full.content) - len(compact.content) >= len(resume)
    # Compatibility callers still get the complete text, and compact refreshes
    # preserve the exact same metadata, role scope and document versions.
    assert desk_client.get('/api/bootstrap?compact=true').json() == expected
    assert_no_private_data(compact.json())


def test_anonymous_combined_startup_does_not_query_crm(desk_engine, desk_client):
    desk_client.cookies.clear()
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get('/api/auth/session?workspace=true&compact=true')
    assert response.json() == {'me': None}
    assert statements == []


def test_revoked_combined_startup_never_returns_cached_workspace(desk_engine, desk_client):
    assert desk_client.get('/api/auth/session?workspace=true').json()['me']['id'] == 'schema-user'
    with desk_engine.begin() as connection:
        connection.execute(SessionRow.__table__.delete().where(SessionRow.id == 'schema-existing-session'))
    with recorded_statements(desk_engine) as statements:
        response = desk_client.get('/api/auth/session?workspace=true&compact=true')
    assert response.json() == {'me': None}
    assert_select_budget(statements, 1)
    desk_client.cookies.set(SESSION_COOKIE, 'other-existing-session')
    staff = desk_client.get('/api/auth/session?workspace=true&compact=true').json()
    assert staff['me']['id'] == 'schema-staff'
    assert all(row['ownerId'] == 'schema-staff' for row in staff['todos'])
    assert {row['id'] for row in staff['notifications']} == {'other-notification'}
