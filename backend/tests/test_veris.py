"""
Polling a Veris job. The deployment only accepts queued jobs, so this loop is what
stands between a recruiter dropping a file and the form filling in.
"""

from types import SimpleNamespace

import pytest
from veriis import FileDescriptor
from veriis.models import AadhaarResponse, JobResponse, PassportResponse, ResumeResponse

from app.integrations.ocr import client as ocr_client
from app.integrations.ocr.client import ScanFailed, ScanTimedOut, wait_for_job


def job(status: str, **extra) -> JobResponse:
    return JobResponse.model_validate(
        {
            "job_id": "job_1",
            "mode": "resume",
            "filename": "cv.pdf",
            "status": status,
            "attempts": 1,
            "max_attempts": 3,
            "submitted_at": "2026-10-18T18:00:00Z",
            **extra,
        }
    )


class FakeJobs:
    """Replays the statuses Veris would report, one per poll."""

    def __init__(self, *statuses: JobResponse):
        self.statuses = list(statuses)
        self.polls = 0

    def get(self, job_id: str) -> JobResponse:
        assert job_id == "job_1"
        self.polls += 1
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_waits_through_queued_and_running_until_the_job_succeeds():
    clock = FakeClock()
    jobs = FakeJobs(job("queued"), job("running"), job("succeeded", result={"name": "A"}))

    done = wait_for_job(jobs, "job_1", deadline=60, interval=2, sleep=clock.sleep, clock=clock)

    assert done.result == {"name": "A"}
    assert jobs.polls == 3


def test_a_failed_job_raises_with_the_reason_veris_gave():
    clock = FakeClock()
    jobs = FakeJobs(job("running"), job("failed", error={"code": "unreadable", "message": "No text found"}))

    with pytest.raises(ScanFailed, match="No text found"):
        wait_for_job(jobs, "job_1", deadline=60, interval=2, sleep=clock.sleep, clock=clock)


def test_a_failed_job_without_a_reason_still_raises():
    clock = FakeClock()

    with pytest.raises(ScanFailed):
        wait_for_job(FakeJobs(job("failed")), "job_1", deadline=60, interval=2, sleep=clock.sleep, clock=clock)


def test_gives_up_at_the_deadline_instead_of_holding_the_request_forever():
    clock = FakeClock()
    jobs = FakeJobs(job("running"))

    with pytest.raises(ScanTimedOut):
        wait_for_job(jobs, "job_1", deadline=10, interval=2, sleep=clock.sleep, clock=clock)

    # Polled at 0, 2, 4, 6, 8, 10 — then stops, rather than sleeping past the deadline.
    assert jobs.polls == 6
    assert clock.now == 10


class FakeProvider:
    """Only queued jobs exist; touching a synchronous endpoint fails the test."""

    def __init__(self, jobs):
        self.jobs = jobs
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def __getattr__(self, name):
        raise AssertionError(f"Unexpected provider endpoint: {name}")


class SubmittingJobs(FakeJobs):
    def __init__(self, *statuses):
        super().__init__(*statuses)
        self.submissions = []

    def submit(self, document, *, mode, idempotency_key):
        self.submissions.append((document, mode, idempotency_key))
        return SimpleNamespace(job_id="job_1")


def install_provider(monkeypatch, jobs, clock):
    provider = FakeProvider(jobs)

    def construct(**options):
        assert options == {
            "base_url": "https://ocr.invalid", "api_key": "synthetic-key",
            "timeout": 30.0, "max_retries": 0,
        }
        return provider

    monkeypatch.setattr(ocr_client, "VerisOCR", construct)
    # Exercise the real polling loop without waiting on wall time or networking.
    monkeypatch.setattr(ocr_client, "wait_for_job", lambda jobs, job_id: wait_for_job(
        jobs, job_id, deadline=3, interval=2, sleep=clock.sleep, clock=clock,
    ))
    return provider


@pytest.mark.parametrize(
    ("kind", "model"),
    [("resume", ResumeResponse), ("passport", PassportResponse), ("aadhaar", AadhaarResponse)],
)
def test_scan_forwards_idempotency_key_to_one_submission(monkeypatch, kind, model):
    clock = FakeClock()
    result = {
        "request_id": "synthetic-result", "processing_time_ms": 1,
        **({"confidence": 0.95, "mrz": {}} if kind == "passport" else {"name": "Alice Example"}),
    }
    if kind == "aadhaar":
        result = {
            "request_id": "synthetic-result",
            "aadhaar": {"name": "Alice Example", "masked_aadhaar_number": "XXXX XXXX 0000"},
            "page_count": 1, "languages_used": "eng", "total_processing_time_ms": 1,
        }
    result["unmodelled_key"] = {"kept": [1]}
    jobs = SubmittingJobs(
        job("queued", mode=kind),
        job("succeeded", mode=kind, result=result),
    )
    provider = install_provider(monkeypatch, jobs, clock)
    document = FileDescriptor(b"synthetic-selected-pdf", filename=f"{kind}.pdf")

    response = ocr_client.scan(
        kind, document, base_url="https://ocr.invalid", api_key="synthetic-key",
        idempotency_key="crm-stable-selected-pages",
    )

    # The provider's result comes back exactly as returned, not as a rebuilt model.
    assert response == result and isinstance(response, dict)
    model.model_validate(response)
    assert jobs.submissions == [(document, kind, "crm-stable-selected-pages")]
    assert jobs.polls == 2
    assert provider.closed


@pytest.mark.parametrize("pending_status", ["queued", "running"])
def test_pending_scan_times_out_without_resubmission_or_synchronous_fallback(monkeypatch, pending_status):
    clock = FakeClock()
    jobs = SubmittingJobs(job(pending_status))
    provider = install_provider(monkeypatch, jobs, clock)
    document = FileDescriptor(b"synthetic-selected-pdf", filename="resume.pdf")

    with pytest.raises(ScanTimedOut, match="job_1"):
        ocr_client.scan(
            "resume", document, base_url="https://ocr.invalid", api_key="synthetic-key",
            idempotency_key="crm-existing-job",
        )

    assert jobs.submissions == [(document, "resume", "crm-existing-job")]
    assert jobs.polls == 3
    assert clock.now == 3
    assert provider.closed
