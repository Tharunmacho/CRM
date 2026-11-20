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
    assert_select_budget(statements, 3)  # sign-in check, snapshot and unread badge
    assert_no_private_data(response.json())


def test_compact_startup_omits_only_resume_text(desk_engine, desk_client):
    # A tab still running the previous version of the app asks for `compact=true` alone: it keeps the exact earlier shape.
    resume = 'Synthetic welding experience. ' * 5000
    with desk_engine.begin() as connection:
        connection.execute(update(Candidate).values(resume_text=resume))
    full = desk_client.get('/api/bootstrap')
    with recorded_statements(desk_engine) as statements:
        compact = desk_client.get('/api/auth/session?workspace=true&compact=true')
    assert compact.status_code == 200
    assert_select_budget(statements, 3)  # sign-in check, snapshot and unread badge
    expected = full.json()
    assert expected['candidates'][0]['resumeText'] == resume
    for row in expected['candidates']:
        row['resumeText'] = ''
    assert compact.json() == expected
    assert len(full.content) - len(compact.content) >= len(resume)
    assert desk_client.get('/api/bootstrap?compact=true').json() == expected
    assert 'etag' not in compact.headers and 'revision' not in compact.json()
    assert_no_private_data(compact.json())


def expand_compact(payload):
    """What the browser does with a compact desk (frontend/src/shared/state/desk-wire.ts): put the trimmed keys back and
    rebuild each candidate's slot list from `documentCounts`. Returns the desk as a full snapshot would carry it, except
    that the slots hold no versions."""
    import copy
    desk = {key: value for key, value in payload.items() if key not in ("rowDefaults", "documentLabels", "revision")}
    defaults = payload["rowDefaults"]
    for name in ("candidates", "applications", "tasks", "orders", "companies", "clients"):
        desk[name] = [{**{k: copy.deepcopy(v) for k, v in defaults[name].items() if k not in row}, **row} for row in desk[name]]
    for application in desk["applications"]:
        application["stages"] = [{**{k: v for k, v in defaults["stages"].items() if k not in stage}, **stage} for stage in application["stages"]]
    for candidate in desk["candidates"]:
        candidate["documents"] = [{"key": key, "label": payload["documentLabels"][key], "versions": [], "fileUploaded": True} for key in candidate["documentCounts"]]
    return desk


def summary_of(full):
    """The same snapshot as the full read, reduced to what the compact desk promises."""
    expected = dict(full)
    for row in expected["candidates"]:
        row["resumeText"] = ""
        row["documentCounts"] = {slot["key"]: len(slot["versions"]) for slot in row["documents"] if slot["versions"]}
        row["documents"] = [{"key": slot["key"], "label": slot["label"], "versions": [], "fileUploaded": True} for slot in row["documents"] if slot["versions"]]
    return expected


def test_slim_startup_is_the_full_desk_without_resume_text_and_with_document_counts(desk_engine, desk_client):
    resume = 'Synthetic welding experience. ' * 5000
    with desk_engine.begin() as connection:
        connection.execute(update(Candidate).values(resume_text=resume))
    full = desk_client.get('/api/bootstrap')
    with recorded_statements(desk_engine) as statements:
        compact = desk_client.get('/api/auth/session?workspace=true&compact=true&slim=true')
    assert compact.status_code == 200
    assert_select_budget(statements, 3)  # sign-in check, snapshot and unread badge
    assert full.json()['candidates'][0]['resumeText'] == resume
    expected = summary_of(full.json())
    assert any(row['documentCounts'] for row in expected['candidates'])  # the fixture's passport scan
    expanded = expand_compact(compact.json())
    assert expanded == expected
    assert len(full.content) - len(compact.content) >= len(resume)
    # The same compact desk comes from /bootstrap, and no file name, id or uploader travels in it.
    assert desk_client.get('/api/bootstrap?compact=true&slim=true').json() == compact.json()
    files = [version for row in full.json()['candidates'] for slot in row['documents'] for version in slot['versions']]
    assert files and not any(version['id'] in compact.text or version['name'] in compact.text for version in files)
    assert_no_private_data(compact.json())


def test_slim_rows_leave_out_keys_that_hold_their_default(desk_client):
    compact = desk_client.get('/api/bootstrap?compact=true&slim=true').json()
    candidate = compact['candidates'][0]
    defaults = compact['rowDefaults']['candidates']
    assert defaults['travellerId'] is None and defaults['languages'] == [] and defaults['resumeText'] == ''
    assert 'travellerId' not in candidate and 'resumeText' not in candidate and 'documents' not in candidate
    assert candidate['documentCounts'] == {'resume': 2, 'passport': 1}
    assert all('escalated' not in task for task in compact['tasks'] if task.get('escalated') is False)
    # A required field is never trimmed, and a value that differs from its default is kept.
    assert {'id', 'name', 'phone', 'source', 'status'} <= set(candidate)
    assert candidate['languages'] == ['Tamil', 'English'] and candidate['totalExperience'] == 8
    assert compact['documentLabels']['passport'] == 'Passport'


def test_the_desk_document_slots_match_the_labelled_ones():
    from app.api.routers.data import DOCUMENT_SLOTS
    from app.services.desk_snapshot import DESK_DOCUMENT_SLOTS
    assert DESK_DOCUMENT_SLOTS == tuple(key for key, _ in DOCUMENT_SLOTS)


def test_the_slim_desk_carries_a_revision_that_is_also_its_etag(desk_client):
    first = desk_client.get('/api/auth/session?workspace=true&compact=true&slim=true')
    revision = first.json()['revision']
    assert first.headers['etag'] == f'"{revision}"'
    assert first.headers['cache-control'] == 'no-store'  # private, never kept by the browser or a shared cache
    assert desk_client.get('/api/bootstrap?compact=true&slim=true').headers['cache-control'] == 'private, no-store'
    full = desk_client.get('/api/bootstrap')
    assert 'etag' not in full.headers and 'revision' not in full.json()  # the full payload keeps its exact old shape


def test_an_unchanged_desk_answers_304_with_no_body_and_a_changed_one_answers_in_full(desk_engine, desk_client):
    first = desk_client.get('/api/bootstrap?compact=true&slim=true')
    tag = first.headers['etag']
    for path in ('/api/bootstrap?compact=true&slim=true', '/api/auth/session?workspace=true&compact=true&slim=true'):
        same = desk_client.get(path, headers={'If-None-Match': tag})
        assert same.status_code == 304 and same.content == b''
        assert same.headers['etag'] == tag and 'content-encoding' not in same.headers
        assert same.headers['cache-control'] in ('private, no-store', 'no-store')
    assert desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': f'W/{tag}'}).status_code == 304  # a proxy may weaken it
    assert desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': f'"other", {tag}'}).status_code == 304
    assert desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': '*'}).status_code == 200
    assert desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': '"stale"'}).status_code == 200
    with desk_engine.begin() as connection:
        connection.execute(update(Candidate).values(name='Renamed so the revision moves'))
    changed = desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': tag})
    assert changed.status_code == 200 and changed.headers['etag'] != tag
    assert changed.json()['candidates'][0]['name'] == 'Renamed so the revision moves'
    assert desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': changed.headers['etag']}).status_code == 304


def test_a_revision_is_never_valid_for_another_person_or_an_ended_session(desk_engine, desk_client):
    mine = desk_client.get('/api/bootstrap?compact=true&slim=true').headers['etag']
    desk_client.cookies.set(SESSION_COOKIE, 'other-existing-session')
    other = desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': mine})
    assert other.status_code == 200 and other.json()['me']['id'] == 'schema-staff' and other.headers['etag'] != mine
    with desk_engine.begin() as connection:
        connection.execute(SessionRow.__table__.delete().where(SessionRow.id == 'other-existing-session'))
    assert desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'If-None-Match': other.headers['etag']}).status_code == 401
    assert desk_client.get('/api/auth/session?workspace=true&compact=true&slim=true', headers={'If-None-Match': other.headers['etag']}).json() == {'me': None}


def test_the_slim_desk_stays_gzipped_and_a_304_is_empty(desk_client):
    zipped = desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'accept-encoding': 'gzip'})
    assert zipped.headers['content-encoding'] == 'gzip' and zipped.headers['vary'] == 'Accept-Encoding'
    again = desk_client.get('/api/bootstrap?compact=true&slim=true', headers={'accept-encoding': 'gzip', 'If-None-Match': zipped.headers['etag']})
    assert again.status_code == 304 and again.content == b'' and again.headers['vary'] == 'Accept-Encoding'


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
