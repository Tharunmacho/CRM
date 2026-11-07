from datetime import timedelta
"""Two installations on one database: a worker reads only the files it holds, and never fails a job for one it does not."""
import io
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app import diag_extraction
from app.db.models import DocumentExtractionJob
from app.services import extraction_queue, manual_extraction
from app.services.instance_guard import NOT_HERE, instance_name, stored_here
from test_database_naming import schema_database  # noqa: F401
from test_document_extraction import pdf
from test_manual_extraction import intake, process, start, upload  # noqa: F401
from test_ocr_classification import CV


def test_the_name_is_configured_or_derived_from_the_host_and_the_storage_location(tmp_path):
    first = SimpleNamespace(storage_dir=str(tmp_path / "a"), storage_backend="local")
    second = SimpleNamespace(storage_dir=str(tmp_path / "b"), storage_backend="local")
    assert instance_name(first) != instance_name(second)  # different storage, different name
    assert instance_name(first) == instance_name(SimpleNamespace(storage_dir=str(tmp_path / "a" / ".." / "a"), storage_backend="local"))
    assert instance_name(SimpleNamespace(instance_name="  laptop  ", storage_dir="x")) == "laptop"


def test_a_new_job_is_stamped_with_the_installation_that_saved_it(intake):
    job = upload(intake, pdf(CV))
    with intake.sessions() as db:
        assert db.get(DocumentExtractionJob, job["id"]).instance == instance_name(intake.settings)


def test_a_worker_claims_its_own_and_unstamped_jobs_but_not_those_of_another_installation(intake):
    own, unstamped, foreign = (upload(intake, pdf(CV + f"\nvariant {n}")) for n in range(3))
    for job in (own, unstamped, foreign):
        start(intake, job["id"])
    with intake.sessions() as db:
        db.get(DocumentExtractionJob, unstamped["id"]).instance = None
        db.get(DocumentExtractionJob, foreign["id"]).instance = "another-machine-12345678"
        db.commit()
    queue = extraction_queue.manual_queue(intake.sessions, intake.settings)

    claimed = queue.claim("worker:test", 10, 180, now=extraction_queue.utcnow() + timedelta(seconds=2))

    assert set(claimed) == {own["id"], unstamped["id"]}
    with intake.sessions() as db:
        assert db.get(DocumentExtractionJob, foreign["id"]).status == "pending"  # untouched, attempts 0
        assert db.get(DocumentExtractionJob, foreign["id"]).attempts == 0


def test_a_job_whose_file_is_not_in_this_storage_is_handed_back_without_an_attempt_never_failed(intake):
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    with intake.sessions() as db:
        row = db.get(DocumentExtractionJob, job["id"])
        row.instance = None  # written by a copy whose storage is elsewhere
        key = row.object_key
        db.commit()
    Path(intake.settings.storage_dir, key).unlink()
    assert not stored_here(intake.settings, key)
    queue = extraction_queue.manual_queue(intake.sessions, intake.settings)

    for _ in range(5):  # claiming it over and over never uses up its attempts
        with intake.sessions() as db:
            db.get(DocumentExtractionJob, job["id"]).next_attempt_at = extraction_queue.utcnow()
            db.commit()
        claimed = queue.claim("worker:test", 1, 180)
        assert claimed == [job["id"]]
        queue.runner(claimed[0], "worker:test")

    with intake.sessions() as db:
        row = db.get(DocumentExtractionJob, job["id"])
        assert row.status == "retry" and row.attempts == 0 and row.claimed_by is None
        assert row.last_error == NOT_HERE and row.next_attempt_at > extraction_queue.utcnow()
        assert row.finished_at is None


def test_the_diagnostic_names_this_installation_and_flags_jobs_of_another(intake):
    mine, theirs = upload(intake, pdf(CV)), upload(intake, pdf(CV + "\nother"))
    with intake.sessions() as db:
        db.get(DocumentExtractionJob, theirs["id"]).instance = "another-machine-12345678"
        db.commit()

    out = io.StringIO()
    with redirect_stdout(out):
        diag_extraction.main(sessions=intake.sessions, settings=intake.settings)

    text = out.getvalue()
    assert f"This installation is '{instance_name(intake.settings)}'" in text
    assert "stamped by another installation ('another-machine-12345678')" in text


# ---- older readers must not see this installation's jobs ----------------------------------------------------------------

# The claim of the readers that predate the instance stamp (git HEAD: document_extraction_worker.run_cycle).
OLD_CLAIM = ("SELECT id FROM document_extraction_jobs WHERE status IN ('pending','processing','retry') "
             "AND next_attempt_at <= now() ORDER BY next_attempt_at LIMIT 10")


def old_reader_sees(env):
    from sqlalchemy import text
    with env.sessions() as db:
        return {row.id for row in db.execute(text(OLD_CLAIM))}


def test_an_older_reader_finds_nothing_to_claim_among_this_installations_jobs(intake):
    stamped = upload(intake, pdf(CV))
    start(intake, stamped["id"])
    legacy = upload(intake, pdf(CV + "\nlegacy"))
    start(intake, legacy["id"])
    with intake.sessions() as db:
        db.get(DocumentExtractionJob, legacy["id"]).instance = None  # as written by older code
        db.flush()
        db.execute(__import__("sqlalchemy").text("UPDATE document_extraction_jobs SET next_attempt_at = now() WHERE id = :i"), {"i": legacy["id"]})
        db.commit()

    assert old_reader_sees(intake) == {legacy["id"]}  # an unstamped row is still an older installation's business

    with intake.sessions() as db:
        row = db.get(DocumentExtractionJob, stamped["id"])
        assert row.status == "pending" and row.next_attempt_at.year == 2100 and row.ready_at is not None
        assert row.ready_at.year < 2100  # the real due time is kept apart


def test_an_older_api_or_reader_writing_a_due_time_cannot_make_a_stamped_job_visible(intake):
    from sqlalchemy import text
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    with intake.sessions() as db:  # what the older retry endpoint and the older failure handler do
        db.execute(text("UPDATE document_extraction_jobs SET status = 'retry', next_attempt_at = now() WHERE id = :i"), {"i": job["id"]})
        db.execute(text("UPDATE document_extraction_jobs SET next_attempt_at = now() + interval '30 seconds' WHERE id = :i"), {"i": job["id"]})
        db.commit()
    assert old_reader_sees(intake) == set()
    with intake.sessions() as db:
        row = db.get(DocumentExtractionJob, job["id"])
        assert row.next_attempt_at.year == 2100 and row.ready_at > extraction_queue.utcnow()  # a later due time is kept too


def test_this_installation_claims_by_the_real_due_time_and_reports_the_queue_by_it(intake):
    first, second = upload(intake, pdf(CV)), upload(intake, pdf(CV + "\nsecond"))
    for job in (first, second):
        start(intake, job["id"])
    queue = extraction_queue.manual_queue(intake.sessions, intake.settings)
    with intake.sessions() as db:
        from sqlalchemy import text
        db.execute(text("UPDATE document_extraction_jobs SET next_attempt_at = now() + interval '1 hour' WHERE id = :i"), {"i": second["id"]})
        db.commit()
        jobs = list(db.scalars(select(DocumentExtractionJob)))
        positions = extraction_queue.queue_positions(db, jobs)
    assert positions[first["id"]] == {"queuePosition": 1, "aheadOfYou": 0}

    assert queue.claim("worker:test", 10, 180, now=extraction_queue.utcnow() + timedelta(seconds=2)) == [first["id"]]  # the one due later waits, though both are hidden from old readers

    with intake.sessions() as db:
        db.execute(__import__("sqlalchemy").text("UPDATE document_extraction_jobs SET next_attempt_at = now() WHERE id = :i"), {"i": second["id"]})
        db.commit()
    assert queue.claim("worker:test", 10, 180, now=extraction_queue.utcnow() + timedelta(seconds=2)) == [second["id"]]


def test_a_stamped_job_is_still_read_and_every_retry_stays_hidden(intake, monkeypatch):
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **k: 1 / 0)
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    queue = extraction_queue.manual_queue(intake.sessions, intake.settings)
    claimed = queue.claim("worker:test", 1, 180)
    queue.runner(claimed[0], "worker:test")  # fails once: the backoff sets a later due time
    with intake.sessions() as db:
        row = db.get(DocumentExtractionJob, job["id"])
        assert row.status == "retry" and row.attempts == 1 and row.next_attempt_at.year == 2100
        assert row.ready_at > extraction_queue.utcnow()
    assert old_reader_sees(intake) == set()


def test_the_job_json_carries_the_fields_the_upload_screen_needs(intake, monkeypatch):
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **k: 1 / 0)
    job = upload(intake, pdf(CV))
    started = intake.client.post("/api/ocr/jobs/" + job["id"] + "/retry").json()
    assert started["status"] == "pending" and started["attempts"] == 0 and started["maxAttempts"] == 4
    assert started["readyAt"] is not None and started["readyAt"].startswith("20") and not started["readyAt"].startswith("2100")
    assert started["lastErrorHint"] is None

    queue = extraction_queue.manual_queue(intake.sessions, intake.settings)
    queue.runner(queue.claim("worker:test", 1, 180)[0], "worker:test")

    again = intake.client.get("/api/ocr/jobs/" + job["id"]).json()
    assert again["status"] == "retry" and again["attempts"] == 1
    assert again["lastErrorHint"] == "Reading failed (ZeroDivisionError)."
    assert again["readyAt"] > started["readyAt"] and not again["readyAt"].startswith("2100")
    assert "reader" in again or again["waitingReason"] in (None, "queue", "reader_not_running")
