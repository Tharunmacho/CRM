"""A failed reading attempt must say why (type and place, never content), and what the worker saves must always be JSON."""
import io
import logging
from contextlib import redirect_stdout

import numpy as np
from sqlalchemy import select

from app import diag_extraction
from app.db.models import DocumentExtractionJob
from app.services import manual_extraction
from test_document_extraction import pdf
from test_database_naming import schema_database  # noqa: F401
from test_manual_extraction import intake, process, start, upload  # noqa: F401
from test_ocr_classification import CV


def test_a_failed_attempt_records_its_type_and_place_and_the_log_line_is_emitted(intake, monkeypatch, caplog):
    def explode(*args, **kwargs):
        raise RuntimeError("SECRET DOCUMENT TEXT must never be stored")

    monkeypatch.setattr(manual_extraction, "extract_attachment", explode)
    job = upload(intake, pdf(CV))
    start(intake, job["id"])

    with caplog.at_level(logging.WARNING, logger="app.services.manual_extraction"):
        process(intake, job["id"])

    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        failure, reader = row.state["failure"], row.state["reader"]
        assert row.status == "retry" and row.attempts == 1
        assert failure["type"] == "RuntimeError" and failure["attempt"] == 1
        assert failure["where"].startswith("services.manual_extraction:process_job:")
        assert failure["version"] and reader["version"] == failure["version"] and reader["pid"]
        assert row.last_error.startswith("Reading failed (RuntimeError)") and "server log" in row.last_error
        assert "SECRET" not in str(row.state["failure"]) + row.last_error
    records = [r for r in caplog.records if "failed (RuntimeError" in r.getMessage()]
    assert records and records[0].levelno == logging.WARNING and records[0].exc_info  # the traceback is in the log line


def test_the_diagnostic_prints_the_failure_and_whether_current_code_handled_the_job(intake, monkeypatch):
    monkeypatch.setattr(manual_extraction, "extract_attachment", lambda *a, **k: 1 / 0)
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    process(intake, job["id"])
    # A row an older worker wrote: failed, with neither a failure nor a reader recorded.
    with intake.sessions() as db:
        old = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        db.add(DocumentExtractionJob(id="old-job", owner_id=old.owner_id, group_id=old.group_id, kind="resume", filename="old.pdf",
            content_type="application/pdf", object_key=old.object_key, content_hash="x", size_kb=1, status="failed", attempts=4, last_error="old"))
        db.commit()

    out = io.StringIO()
    with redirect_stdout(out):
        diag_extraction.main(sessions=intake.sessions)

    text = out.getvalue()
    assert "failure: type=ZeroDivisionError where=services.manual_extraction:process_job" in text
    assert "handled by reader version=" in text
    assert "handled by: OLDER CODE" in text


def test_state_with_numpy_numbers_and_sets_is_saved_as_json(intake, monkeypatch):
    real = manual_extraction.extract_attachment

    def numpy_output(data, **kwargs):
        output = real(data, **kwargs)
        output["coverage"] = {"score": np.float32(1.5), "pages": {3, 1, 2}, "count": np.int64(4)}
        return output

    monkeypatch.setattr(manual_extraction, "extract_attachment", numpy_output)
    job = upload(intake, pdf(CV))
    start(intake, job["id"])
    process(intake, job["id"])

    with intake.sessions() as db:
        row = db.scalar(select(DocumentExtractionJob).where(DocumentExtractionJob.id == job["id"]))
        assert row.status in {"completed", "needs_review"}
        assert row.state["output"]["coverage"] == {"score": 1.5, "pages": [1, 2, 3], "count": 4}
