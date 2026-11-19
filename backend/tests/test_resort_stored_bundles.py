"""Combined files stored by older code are sorted in the background, once.

A candidate saved before pages were sorted by content has one bundle (resume + passport + certificates + ID cards) in the Resume field and
empty fields beside it. `read-pending` (the form opening) and the admin / manager sweep `POST /api/candidates/resort` queue ONE `resort` job
per stored file; the reading worker sorts it and files the pieces in their own fields exactly like an upload-time split, keeps the whole
file as the source evidence and queues the new pieces for reading. A file is never sorted twice, other candidates are untouched, a sort
that was cut short is never marked as done.

DB-backed (disposable PostgreSQL); synthetic documents with a text layer, cut by the real local engine; the online reader is faked.
"""
import hashlib
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import undefer

from app.db.models import Attachment, AuditEntry, Candidate, DocumentExtractionJob, Role, Source
from app.services import bulk_candidate_import as bulk, manual_extraction, resort_bundles
from app.services.extraction_queue import manual_queue
from ocr_synthetic import composite_pdf
from test_auto_reading import (BUNDLE, OWN_FIELDS, bundle, bytes_of, install_reader, page_texts, read_pending, run_worker, slots,  # noqa: F401
                               store, world)
from test_database_naming import schema_database  # noqa: F401  (fixture)
from test_document_splitting_uploads import desk, send  # noqa: F401  (desk is a fixture)

PIECE_SLOTS = {"resume", "passport", "aadhaar", "education_certificate", "signed_offer", "visa", "medical", "pcc", "air_ticket"}
READABLE = {"resume", "passport", "aadhaar", "education_certificate"}
DEFERRED = (undefer(DocumentExtractionJob.state), undefer(DocumentExtractionJob.result), undefer(DocumentExtractionJob.draft))


@pytest.fixture(autouse=True)
def _test_settings(desk, monkeypatch):
    monkeypatch.setattr(resort_bundles, "get_settings", lambda: desk.settings)      # never the real .env


def legacy_read(env, row, candidate="candidate", status="completed"):
    """The reading job older code made for this stored file: it exists, but it filed nothing and saved no page kinds."""
    with env.sessions() as db:
        db.add(DocumentExtractionJob(id=str(uuid.uuid4()), owner_id="staff", group_id=str(uuid.uuid4()), kind=row.slot, filename=row.file_name,
                                     content_type="application/pdf", object_key=row.object_key, content_hash=row.content_hash, size_kb=1,
                                     status=status, candidate_id=candidate, attachment_id=row.id, state={},
                                     draft={"applicationForm": {"candidateId": candidate, "slot": row.slot, "attachmentId": row.id}}))
        db.commit()


def resorts(env, candidate="candidate"):
    with env.sessions() as db:
        return list(db.scalars(select(DocumentExtractionJob).options(*DEFERRED).where(DocumentExtractionJob.kind == "resort", DocumentExtractionJob.group_id == candidate)
                               .order_by(DocumentExtractionJob.created_at)))


def readings(env, candidate="candidate"):
    with env.sessions() as db:
        return list(db.scalars(select(DocumentExtractionJob).options(*DEFERRED).where(DocumentExtractionJob.candidate_id == candidate, DocumentExtractionJob.kind != "resort")
                               .order_by(DocumentExtractionJob.created_at)))


def add_candidate(env, identifier, owner="staff"):
    with env.sessions() as db:
        db.add(Candidate(id=identifier, ref="CND-" + identifier[-3:], name=identifier.title(), phone="90000" + str(abs(hash(identifier)) % 100000).zfill(5),
                         source=Source.walk_in, owner_id=owner))
        db.commit()


def snapshot(env, candidate="candidate"):
    return {slot: [(row.id, row.version, row.content_hash) for row in rows] for slot, rows in slots(env, candidate).items()}


def sweep(env, **body):
    return env.api.post("/api/candidates/resort", json=body)


# ------------------------------------------------------------------ the sort itself

def test_a_stored_bundle_is_sorted_into_its_fields_and_the_pieces_are_queued_for_reading(world, monkeypatch):
    install_reader(monkeypatch)
    whole = bundle()
    row = store(world, "resume", "Alex_Example-Resume.pdf", whole)          # imported earlier: the whole bundle sits in the Resume field
    legacy_read(world, row)                                                  # ... and older code read it without filing anything
    assert set(slots(world)) == {"resume"}

    result = read_pending(world).json()
    assert result["sorting"] == 1 and result["changed"] is True and result["queued"] == 0
    assert [(job.status, job.owner_id) for job in resorts(world)] == [("pending", "staff")]
    assert set(slots(world)) == {"resume"}                                   # nothing is read in the request

    run_worker(world)                                                        # the reading worker sorts it
    [job] = resorts(world)
    assert job.status == "completed" and job.result["outcome"] == "sorted" and job.state["resort"]["outcome"] == "sorted"
    filed = slots(world)
    assert set(filed) == PIECE_SLOTS | {"source_bundle"} and all(len(rows) == 1 for rows in filed.values())
    assert filed["source_bundle"][0].id == row.id and bytes_of(world, filed["source_bundle"][0]) == whole      # the whole file is kept, untouched
    assert len(page_texts(bytes_of(world, filed["resume"][0]))) == 2 and len(page_texts(bytes_of(world, filed["passport"][0]))) == 2
    originals = page_texts(whole)
    seen = set()
    for slot, rows in filed.items():
        if slot != "source_bundle":
            for text in page_texts(bytes_of(world, rows[0])):
                seen.update(number for number, original in enumerate(originals, 1) if original == text)
    assert seen == set(range(1, len(originals) + 1))                        # not one page is in no field
    with world.sessions() as db:
        metas = {entry.meta["slot"]: entry.meta for entry in db.scalars(select(AuditEntry).where(
            AuditEntry.action == "Uploaded document", AuditEntry.entity_id == "candidate")) if entry.meta.get("resorted")}
        done = db.scalar(select(AuditEntry).where(AuditEntry.action == "Sorted a combined file in the background"))
    assert set(metas) == PIECE_SLOTS and metas["passport"]["split_pages"] == [3, 4] and metas["passport"]["source_attachment_id"] == row.id
    assert metas["resume"]["original_name"] == "Alex_Example-Resume.pdf"
    assert done is not None and done.meta["from_slot"] == "resume" and len(done.meta["pieces"]) == len(PIECE_SLOTS)
    # the new resume / passport / Aadhaar / education pieces are queued for the reader; the others only filed
    queued = [(item.draft["applicationForm"]["slot"], item.status) for item in readings(world) if item.attachment_id != row.id]
    assert sorted(queued) == sorted((slot, "pending") for slot in READABLE)

    run_worker(world)                                                        # the pieces are read: the form fills by itself
    with world.sessions() as db:
        candidate = db.get(Candidate, "candidate")
        assert candidate.passport_no == "K1234567" and candidate.field_sources.get("passport.passportNumber") == "extracted"


def test_a_second_run_does_nothing_and_other_candidates_are_untouched(world, monkeypatch):
    install_reader(monkeypatch)
    add_candidate(world, "second")
    for owner in ("candidate", "second"):
        row = store(world, "resume", "Bundle.pdf", bundle(), candidate=owner)
        legacy_read(world, row, owner)
    assert read_pending(world).json()["sorting"] == 1                       # only the candidate whose form was opened
    run_worker(world)
    assert resorts(world, "second") == [] and set(slots(world, "second")) == {"resume"}
    first = snapshot(world)
    assert set(first) == PIECE_SLOTS | {"source_bundle"}

    again = read_pending(world).json()
    assert "sorting" not in again and again["queued"] == 0
    world.actor.id, world.actor.role = "admin", Role.admin
    assert sweep(world, candidateIds=["candidate"]).json()["queued"] == 0
    world.actor.id, world.actor.role = "staff", Role.staff
    run_worker(world)
    run_worker(world)                                                        # the pieces' own readings never sort again
    assert snapshot(world) == first
    outcomes = [job.result["outcome"] for job in resorts(world)]           # one sort; the pieces it filed only got finished markers
    assert outcomes.count("sorted") == 1 and {job.status for job in resorts(world)} == {"completed"}
    assert set(outcomes) - {"sorted"} <= {"already_a_piece", "left_to_reading", "pieces_already_filed"}, outcomes
    assert set(slots(world, "second")) == {"resume"} and len(slots(world, "second")["resume"]) == 1


def test_a_bundle_kept_as_source_evidence_that_was_never_sorted_is_sorted_from_there(world, monkeypatch):
    install_reader(monkeypatch)
    whole = bundle()
    row = store(world, "source_bundle", "Bundle.pdf", whole)                 # never read: nothing else would ever look at it
    assert read_pending(world).json()["sorting"] == 1
    run_worker(world)
    filed = slots(world)
    assert set(filed) == PIECE_SLOTS | {"source_bundle"}
    assert [(item.id, item.version) for item in filed["source_bundle"]] == [(row.id, 1)]
    assert [job.result["outcome"] for job in resorts(world)] == ["sorted"]


def test_a_passport_field_bundle_moves_to_the_source_evidence_and_a_resume_never_replaces_an_existing_one(world, monkeypatch):
    install_reader(monkeypatch)
    resume = store(world, "resume", "My-Resume.pdf", composite_pdf(["unrelated"]))      # a real resume the candidate already has
    whole = bundle()
    row = store(world, "passport", "All-documents.pdf", whole)
    legacy_read(world, row)
    assert read_pending(world).json()["sorting"] == 1
    run_worker(world)
    filed = slots(world)
    assert [item.id for item in filed["resume"]] == [resume.id]                # the candidate's resume is still the current one
    assert [item.id for item in filed["source_bundle"]] == [row.id] and "passport" in filed and filed["passport"][0].id != row.id
    assert len(page_texts(bytes_of(world, filed["other"][0]))) == 2           # the resume pages of the bundle are in Other documents
    assert bytes_of(world, filed["source_bundle"][0]) == whole


def test_a_file_that_is_one_document_is_marked_and_left_alone(world, monkeypatch):
    install_reader(monkeypatch)
    row = store(world, "other", "Letter.pdf", bundle(["resume_1", "resume_2"]))
    assert read_pending(world).json()["sorting"] == 1
    run_worker(world)
    [job] = resorts(world)
    assert job.status == "completed" and job.result["outcome"] == "one_document"
    assert [item.id for item in slots(world)["other"]] == [row.id] and set(slots(world)) == {"other"}
    assert "sorting" not in read_pending(world).json()


def test_a_two_page_passport_scan_and_a_single_page_are_not_bundles(world):
    store(world, "passport", "Passport.pdf", composite_pdf(["passport", "passport"]))
    store(world, "other", "One.pdf", composite_pdf(["unrelated"]))
    assert read_pending(world).json()["sorting"] == 1                        # the passport has no job and is left to its reading; the Other file is looked at
    run_worker(world)
    assert sorted(job.result["outcome"] for job in resorts(world)) == ["left_to_reading", "not_a_bundle"]


def test_files_that_need_no_sorting_get_a_finished_marker_and_no_job(world):
    # unread identity files are left to their reading (it sorts them by itself); read-pending still queues that reading
    store(world, "resume", "Bundle.pdf", bundle())
    result = read_pending(world).json()
    assert result["queued"] == 1 and "sorting" not in result
    [marker] = resorts(world)
    assert marker.status == "completed" and marker.result["outcome"] == "left_to_reading"
    assert [item.kind for item in readings(world)] == ["resume"]            # the reading job is the only one the candidate's screens see


def test_a_scan_split_when_it_was_uploaded_is_not_sorted_again(world):
    scan = bundle()
    response = send(world, scan, slot="passport", name="All.pdf", defer="true")
    assert response.status_code == 201 and response.json()["split"]["pieces"]
    before = snapshot(world)
    result = read_pending(world).json()
    assert "sorting" not in result and not any(job.status == "pending" for job in resorts(world))
    assert snapshot(world) == before
    assert {job.result["outcome"] for job in resorts(world)} <= {"pieces_already_filed", "already_a_piece", "left_to_reading"}


# ------------------------------------------------------------------ heavy files and a sort that was cut short

def test_files_over_the_size_or_page_cap_are_never_opened_or_sorted(world, monkeypatch):
    install_reader(monkeypatch)
    monkeypatch.setattr(resort_bundles, "RESORT_MAX_KB", 0)
    store(world, "other", "Big.pdf", bundle())
    result = read_pending(world).json()
    assert "sorting" not in result and [job.result["outcome"] for job in resorts(world)] == ["too_large"]
    assert set(slots(world)) == {"other"}

    monkeypatch.setattr(resort_bundles, "RESORT_MAX_KB", 25 * 1024)
    monkeypatch.setattr(resort_bundles, "RESORT_MAX_PAGES", 3)
    store(world, "source_bundle", "Long.pdf", bundle(BUNDLE, "a twelfth page"))
    assert read_pending(world).json()["sorting"] == 1
    run_worker(world)
    assert sorted(job.result["outcome"] for job in resorts(world)) == ["too_large", "too_long"]
    assert set(slots(world)) == {"other", "source_bundle"}                  # nothing was cut


def test_a_sort_cut_short_is_not_marked_sorted_is_tried_again_and_given_up_after_a_few_attempts(world, monkeypatch):
    install_reader(monkeypatch)
    row = store(world, "source_bundle", "Bundle.pdf", bundle())
    cut = bulk.SplitResult([bulk.Piece(["other"], b"", [])], False, "cut", cut_short=True)
    monkeypatch.setattr(bulk, "split_by_content", lambda *args, **kwargs: cut)
    assert read_pending(world).json()["sorting"] == 1
    for attempt in (1, 2):
        run_worker(world)
        [job] = resorts(world)
        assert job.status == "retry" and job.attempts == attempt and job.state["resort"]["outcome"] is None       # never marked as sorted
        assert set(slots(world)) == {"source_bundle"}
        with world.sessions() as db:                                         # the retry is due
            db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.id == job.id).values(next_attempt_at=func.now()))
            db.commit()
    run_worker(world)
    [job] = resorts(world)
    assert job.status == "dismissed" and job.attempts == resort_bundles.RESORT_MAX_ATTEMPTS and set(slots(world)) == {"source_bundle"}
    assert "sorting" not in read_pending(world).json() and len(resorts(world)) == 1 and run_worker(world) == []   # given up: not queued again, no loop
    assert row.id == slots(world)["source_bundle"][0].id


def test_a_cut_short_sort_that_works_the_second_time_is_filed(world, monkeypatch):
    install_reader(monkeypatch)
    store(world, "source_bundle", "Bundle.pdf", bundle())
    real = bulk.split_by_content
    cut = bulk.SplitResult([bulk.Piece(["other"], b"", [])], False, "cut", failed=True)
    monkeypatch.setattr(bulk, "split_by_content", lambda *args, **kwargs: cut)
    read_pending(world)
    run_worker(world)
    [job] = resorts(world)
    assert job.status == "retry"
    monkeypatch.setattr(bulk, "split_by_content", real)
    with world.sessions() as db:
        db.execute(update(DocumentExtractionJob).where(DocumentExtractionJob.id == job.id).values(next_attempt_at=func.now()))
        db.commit()
    run_worker(world)
    assert [item.status for item in resorts(world)] == ["completed"] and set(slots(world)) == PIECE_SLOTS | {"source_bundle"}


def test_a_stored_file_that_changed_while_waiting_is_not_cut(world, monkeypatch):
    install_reader(monkeypatch)
    row = store(world, "source_bundle", "Bundle.pdf", bundle())
    read_pending(world)
    with world.sessions() as db:                                             # the file was replaced under the same row before the worker came
        db.execute(update(Attachment).where(Attachment.id == row.id).values(content_hash=hashlib.sha256(b"other").hexdigest()))
        db.commit()
    run_worker(world)
    assert [job.result["outcome"] for job in resorts(world)] == ["file_changed"] and set(slots(world)) == {"source_bundle"}


def test_the_job_is_claimed_and_run_by_the_reading_worker_queue(world, monkeypatch):
    install_reader(monkeypatch)
    store(world, "source_bundle", "Bundle.pdf", bundle())
    read_pending(world)
    queue = manual_queue(world.sessions, world.settings)
    [claimed] = queue.claim("worker-1", 5, 60)
    manual_extraction.process_job(claimed, sessions=world.sessions, settings=world.settings, claimed_by="worker-1")
    [job] = resorts(world)
    assert job.id == claimed and job.status == "completed" and job.claimed_by is None and set(slots(world)) == PIECE_SLOTS | {"source_bundle"}


# ------------------------------------------------------------------ who may start it, and how much per call

def test_read_pending_follows_the_same_ownership_rules_for_sorting(world):
    store(world, "source_bundle", "Bundle.pdf", bundle())
    world.actor.id, world.actor.role = "other", Role.staff                  # another recruiter's candidate
    refused = read_pending(world).json()
    assert refused["allowed"] is False and "sorting" not in refused and resorts(world) == []
    world.actor.id, world.actor.role = "manager", Role.manager
    assert read_pending(world).json()["sorting"] == 1
    assert [job.owner_id for job in resorts(world)] == ["manager"]
    world.actor.id, world.actor.role = "staff", Role.staff


def test_the_sweep_is_for_admin_and_manager_only(world):
    store(world, "source_bundle", "Bundle.pdf", bundle())
    for user, role in (("staff", Role.staff), ("other", Role.staff)):
        world.actor.id, world.actor.role = user, role
        assert sweep(world).status_code == 403 and sweep(world, candidateIds=["candidate"]).status_code == 403
    assert resorts(world) == []
    world.actor.id, world.actor.role = "manager", Role.manager
    answer = sweep(world, candidateIds=["candidate"])
    assert answer.status_code == 200 and answer.json() == {"candidates": 1, "queued": 1, "marked": 0, "filed": 0, "readQueued": 0, "skipped": 0, "more": False}
    world.actor.id, world.actor.role = "staff", Role.staff


def test_the_sweep_walks_the_data_bank_a_capped_batch_at_a_time_and_is_idempotent(world, monkeypatch):
    install_reader(monkeypatch)
    monkeypatch.setattr(resort_bundles, "RESORT_SWEEP_CANDIDATES", 2)
    ids = ["cand-a", "cand-b", "cand-c"]
    for identifier in ids:
        add_candidate(world, identifier)
        store(world, "source_bundle", "Bundle.pdf", bundle(), candidate=identifier)
    world.actor.id, world.actor.role = "admin", Role.admin
    first = sweep(world).json()
    assert first == {"candidates": 2, "queued": 2, "marked": 0, "filed": 0, "readQueued": 0, "skipped": 0, "more": True}
    second = sweep(world).json()
    assert second["candidates"] == 1 and second["queued"] == 1 and second["more"] is False
    assert sweep(world).json() == {"candidates": 0, "queued": 0, "marked": 0, "filed": 0, "readQueued": 0, "skipped": 0, "more": False}
    assert [len(resorts(world, identifier)) for identifier in ids] == [1, 1, 1]
    assert set(slots(world, "candidate")) == set()                           # the first candidate of the fixture had no files: untouched
    run_worker(world)
    for identifier in ids:
        assert set(slots(world, identifier)) == PIECE_SLOTS | {"source_bundle"}
    assert [job.status for identifier in ids for job in resorts(world, identifier)] == ["completed"] * 3
    world.actor.id, world.actor.role = "staff", Role.staff


def test_the_sweep_with_ids_is_capped_ignores_unknown_ids_and_reads_nothing_unless_asked(world, monkeypatch):
    monkeypatch.setattr(resort_bundles, "RESORT_SWEEP_CANDIDATES", 1)
    store(world, "resume", "Bundle.pdf", bundle())                           # never read
    world.actor.id, world.actor.role = "admin", Role.admin
    capped = sweep(world, candidateIds=["candidate", "nobody"]).json()
    assert capped["candidates"] == 1 and capped["more"] is True and capped["readQueued"] == 0 and readings(world) == []
    monkeypatch.setattr(resort_bundles, "RESORT_SWEEP_CANDIDATES", 25)
    unknown = sweep(world, candidateIds=["nobody"]).json()
    assert unknown["candidates"] == 0 and unknown["skipped"] == 1
    reading = sweep(world, candidateIds=["candidate"], alsoRead=True).json()
    assert reading["readQueued"] == 1 and [item.kind for item in readings(world)] == ["resume"]
    world.actor.id, world.actor.role = "staff", Role.staff


def test_a_sort_job_never_shows_in_the_upload_screens_job_list(world):
    store(world, "source_bundle", "Bundle.pdf", bundle())
    read_pending(world)
    assert [job.status for job in resorts(world)] == ["pending"]
    listed = world.client.get("/api/ocr/jobs")
    assert listed.status_code == 200 and listed.json() == []
