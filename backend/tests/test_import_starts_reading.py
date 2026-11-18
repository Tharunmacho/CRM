"""An imported candidate's documents are queued for the reader at once, so the name and the details fill in without anyone
pressing "Read documents". The documents are saved first: a failure while queueing never undoes the import.

DB-backed (disposable PostgreSQL); synthetic documents; no real reader (nothing is sent: the jobs only wait in the queue)."""
from sqlalchemy import select

from app.api.routers import candidate_import
from app.db.models import DocumentExtractionJob
from test_bulk_candidate_import import env, send  # noqa: F401  (fixture and helper)
from test_database_naming import schema_database  # noqa: F401  (fixture used by env)


def _jobs(env, candidate_id):
    with env.sessions() as db:
        return list(db.scalars(select(DocumentExtractionJob).where(DocumentExtractionJob.candidate_id == candidate_id)))


def test_an_import_queues_the_resume_and_the_passport_for_reading_by_itself(env, monkeypatch):
    # undo the module-wide switch-off of test_bulk_candidate_import: this test is about the automatic start
    real = candidate_import.__dict__["_start_reading"]
    monkeypatch.undo()
    monkeypatch.setattr(candidate_import, "_start_reading", real)
    result = send(env, "Rashid-demo", ["Rashid-Resume.pdf", "Rashid-passport.pdf"], {"phone": "9876500111"})
    assert result["status"] == "imported"
    assert result.get("readingQueued", 0) >= 1
    kinds = {job.kind for job in _jobs(env, result["candidateId"])}
    assert "resume" in kinds and "passport" in kinds


def test_a_failure_while_queueing_never_undoes_the_import(env, monkeypatch):
    real = candidate_import.__dict__["_start_reading"]
    monkeypatch.undo()
    monkeypatch.setattr(candidate_import, "read_again", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("reader down")))
    monkeypatch.setattr(candidate_import, "_start_reading", real)
    result = send(env, "Dinesh-demo", ["Dinesh-Resume.pdf"], {"phone": "9876500112"})
    assert result["status"] == "imported" and "readingQueued" not in result
    assert _jobs(env, result["candidateId"]) == []
