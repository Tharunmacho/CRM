"""The Import screen's polled calls run a constant number of queries, /identify is bounded, and the Care India ID is taken last.

DB-backed (disposable PostgreSQL): the hosted database costs ~300 ms per statement, so the number of statements is what is pinned.
"""
import json
import threading
from contextlib import contextmanager
from itertools import product
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import Depends
from sqlalchemy import event, text

from app.db.models import Role
from app.services import bulk_candidate_import as bulk, care_india_id, people_by_content as pbc
from test_bulk_candidate_import import TWO_PEOPLE, _check, _read_again, _status, env, send, subject  # noqa: F401  (the fixture and helpers)
from test_database_naming import schema_database  # noqa: F401


@contextmanager
def statements_of(engine):
    seen = []

    def record(connection, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _engine(env):
    return env.sessions.kw["bind"]


def _import_many(env, count):
    return [send(env, f"Cand{n:02}", ["Resume.pdf", "Passport.pdf"], {"phone": f"98765{n:05}"})["candidateId"] for n in range(count)]


# ------------------------------------------------------------------ the in-memory permission check

def test_the_batched_permission_check_is_the_same_rule_as_ensure_can_work_candidate_for_every_role_and_owner():
    from fastapi import HTTPException
    from app.core.deps import candidate_work_refusal, ensure_can_work_candidate
    named = SimpleNamespace(scalar=lambda statement: "Someone")           # the refusal text looks the owner's name up
    for role, owner in product((Role.admin, Role.manager, Role.staff), ("me", "someone-else", None)):
        user = SimpleNamespace(id="me", role=role)
        candidate = SimpleNamespace(id="c", owner_id=owner)
        allowed = subject._may_work(candidate, user)
        assert allowed == (candidate_work_refusal(named, candidate, user) is None), (role, owner)
        try:
            ensure_can_work_candidate(candidate, user, named)
            original = True
        except HTTPException as refusal:
            assert refusal.status_code == 403
            original = False
        assert allowed == original, (role, owner)
    # the cases that matter, spelled out
    admin, manager, staff = (SimpleNamespace(id=key, role=role) for key, role in (("a", Role.admin), ("m", Role.manager), ("s", Role.staff)))
    assert subject._may_work(SimpleNamespace(owner_id=None), admin) and subject._may_work(SimpleNamespace(owner_id="s"), manager)
    assert subject._may_work(SimpleNamespace(owner_id="s"), staff) and not subject._may_work(SimpleNamespace(owner_id="x"), staff)
    assert not subject._may_work(SimpleNamespace(owner_id=None), staff)


def test_status_and_read_again_still_hide_other_recruiters_candidates(env):
    ids = _import_many(env, 3)
    env.actor = SimpleNamespace(id="other", role=Role.staff)
    assert _status(env, ids) == [] and _read_again(env, ids) == 0
    env.actor = SimpleNamespace(id="manager", role=Role.manager)
    assert [item["candidateId"] for item in _status(env, ids)] == ids      # in the order asked


# ------------------------------------------------------------------ statement budgets

def test_status_for_twenty_five_candidates_runs_a_handful_of_statements(env):
    ids = _import_many(env, 25)
    with statements_of(_engine(env)) as seen:
        items = _status(env, ids)
    assert [item["candidateId"] for item in items] == ids
    assert all({d["slot"] for d in item["documents"]} == {"resume", "passport"} for item in items)
    assert len(seen) <= 6, seen
    with statements_of(_engine(env)) as seen:
        assert _status(env, ["nobody"]) == []
    assert len(seen) <= 2


def test_read_again_for_twenty_five_candidates_finds_its_work_in_a_handful_of_statements(env):
    ids = _import_many(env, 25)
    with statements_of(_engine(env)) as seen:
        assert _read_again(env, ids) == 50                   # a resume and a passport each, queued
    assert len([s for s in seen if s.upper().startswith("SELECT") and "pg_notify" not in s]) <= 4, seen
    with statements_of(_engine(env)) as seen:
        assert _read_again(env, ids) == 0                    # everything is queued already: only the looking costs anything
    assert len(seen) <= 10, seen
    assert len([s for s in _status(env, ids) if s["reading"]["total"] == 2]) == 25


def test_a_failed_reading_is_queued_again_through_the_batched_read_again(env):
    from sqlalchemy import select, update
    from app.db.models import DocumentExtractionJob
    [candidate_id] = _import_many(env, 1)
    assert _read_again(env, [candidate_id]) == 2
    with env.sessions() as db:
        db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id,
                                                        DocumentExtractionJob.kind == "passport").values(status="failed"))
        db.commit()
    assert _read_again(env, [candidate_id]) == 1
    with env.sessions() as db:
        assert sorted(job.status for job in db.scalars(select(DocumentExtractionJob))) == ["pending", "pending"]


def test_more_than_two_hundred_ids_are_refused_not_cut(env):
    ids = [f"id-{n}" for n in range(201)]
    assert env.client.post("/api/candidate-import/read-again", json={"candidateIds": ids}).status_code == 422
    assert env.client.post("/api/candidate-import/status", json={"candidateIds": ids}).status_code == 422


# ------------------------------------------------------------------ /identify is bounded

def _identify(env, files, folder="Mixed"):
    spec = {"folder": folder, "files": [{"path": path} for path in files]}
    return env.client.post("/api/candidate-import/identify", data={"spec": json.dumps(spec)},
                           files=[("files", (path, data, "application/pdf")) for path, data in files.items()])


EMPTY = pbc.PeopleResult([], [], [], [], {"files": 0, "pages": 0, "assigned": 0, "unplaced": 0, "unreadable": 0})


def test_identify_refuses_a_folder_over_the_total_size_cap_before_reading_anything(env, monkeypatch):
    monkeypatch.setattr(subject, "MAX_IDENTIFY_TOTAL_BYTES", 1000)
    with patch.object(pbc, "identify_people", side_effect=AssertionError("must not read")):
        refused = _identify(env, {"a.pdf": b"x" * 600, "b.pdf": b"y" * 600})
    assert refused.status_code == 413 and "together" in refused.json()["detail"]
    with patch.object(pbc, "identify_people", return_value=EMPTY):
        assert _identify(env, {"a.pdf": b"x" * 400, "b.pdf": b"y" * 400}).status_code == 200      # under the cap


def test_identify_holds_no_database_connection_while_it_reads(env):
    def actor(db=Depends(subject.get_db)):
        db.execute(text("SELECT 1"))                  # the sign-in check: opens a transaction on the request's session
        return env.actor

    env.client.app.dependency_overrides[subject.current_user] = actor
    pool = _engine(env).pool
    held = []

    def reading(files, **kwargs):
        held.append(pool.checkedout())
        return EMPTY

    with patch.object(pbc, "identify_people", reading):
        assert _identify(env, {"a.pdf": b"%PDF-1.4"}).status_code == 200
    assert held == [0]


def test_a_second_folder_is_told_to_try_again_while_the_slots_are_taken_and_the_slot_is_given_back(env, monkeypatch):
    monkeypatch.setattr(subject, "_IDENTIFY_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(subject, "IDENTIFY_WAIT_SECONDS", 0.4)
    started, release, first = threading.Event(), threading.Event(), {}

    def slow(files, **kwargs):
        started.set()
        release.wait(20)
        return EMPTY

    def run_first():
        first["response"] = _identify(env, {"a.pdf": b"%PDF-1.4"}, "First")

    with patch.object(pbc, "identify_people", slow):
        worker = threading.Thread(target=run_first)
        worker.start()
        assert started.wait(20)
        busy = _identify(env, {"b.pdf": b"%PDF-1.4"}, "Second")
        assert busy.status_code == 429 and "Try again in a minute" in busy.json()["detail"] and busy.headers["retry-after"] == "60"
        release.set()
        worker.join(20)
    assert first["response"].status_code == 200
    with patch.object(pbc, "identify_people", return_value=EMPTY):
        assert _identify(env, {"c.pdf": b"%PDF-1.4"}, "Third").status_code == 200       # the slot came back


def test_a_slot_is_given_back_when_the_reading_fails(env, monkeypatch):
    monkeypatch.setattr(subject, "_IDENTIFY_SLOTS", threading.BoundedSemaphore(1))
    with patch.object(pbc, "identify_people", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            _identify(env, {"a.pdf": b"%PDF-1.4"})
    with patch.object(pbc, "identify_people", return_value=EMPTY):
        assert _identify(env, {"a.pdf": b"%PDF-1.4"}).status_code == 200


# ------------------------------------------------------------------ the folder's plan

def test_a_folder_whose_every_file_was_moved_or_left_out_plans_nothing(env):
    assert _check(env, "pair", TWO_PEOPLE, assign={f"pair/{name}": "" for name in TWO_PEOPLE}) == []
    assert _check(env, "solo", ["Ramesh-resume.pdf"], assign={"solo/Ramesh-resume.pdf": ""}) == []
    assert [p["folder"] for p in _check(env, "pair", TWO_PEOPLE, assign={"pair/Ramesh-resume.pdf": "", "pair/Ramesh-passport.pdf": ""})] == ["pair / Suresh"]


# ------------------------------------------------------------------ the Care India ID is taken after the files are stored

def test_the_care_india_id_lock_is_taken_after_every_file_is_stored(env):
    events = []
    store, allocate = bulk._store_bytes, care_india_id.allocate_care_id

    def storing(*args, **kwargs):
        events.append("store")
        return store(*args, **kwargs)

    def allocating(db):
        events.append("care_id")
        return allocate(db)

    with patch.object(bulk, "_store_bytes", storing), patch.object(care_india_id, "allocate_care_id", allocating):
        result = send(env, "Order", ["Resume.pdf", "Passport.pdf", "Medical.pdf"], {"phone": "9876500999"})
    assert events == ["store", "store", "store", "care_id"]
    assert result["careId"].startswith("CI-")


# ------------------------------------------------------------------ what the screen and the profile are told

def test_read_again_says_how_many_stored_files_can_be_read_so_the_screen_can_tell_nothing_to_read_from_all_read(env):
    [empty] = [send(env, "Ravi", ["Medical.pdf"], {"phone": "9876500501"})["candidateId"]]
    response = env.client.post("/api/candidate-import/read-again", json={"candidateIds": [empty]})
    assert response.json() == {"queued": 0, "readable": 0}                  # only a medical report: nothing to read
    [full] = _import_many(env, 1)
    assert env.client.post("/api/candidate-import/read-again", json={"candidateIds": [full]}).json() == {"queued": 2, "readable": 2}
    assert env.client.post("/api/candidate-import/read-again", json={"candidateIds": [full]}).json() == {"queued": 0, "readable": 2}


def test_imported_documents_waiting_to_be_read_are_offered_extract_details_on_the_profile_until_they_are_read(env):
    """The profile's Documents tab shows the button from `extractionPending`, which the database reports only for a file stored with
    its reading deferred. An import stores without reading, so it must say so."""
    from sqlalchemy import select
    from app.db.models import Attachment
    from app.services.document_reading import pending_expression
    result = send(env, "Anil", ["Resume.pdf", "Passport.pdf", "Medical.pdf"], {"phone": "9876500601"})

    def pending():
        with env.sessions() as db:
            return {slot: flag for slot, flag in db.execute(select(Attachment.slot, pending_expression()).where(
                Attachment.entity_type == "candidate", Attachment.entity_id == result["candidateId"]))}

    assert pending() == {"resume": True, "passport": True, "medical": False}
    assert _read_again(env, [result["candidateId"]]) == 2
    assert pending() == {"resume": False, "passport": False, "medical": False}
