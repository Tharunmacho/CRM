"""
Polling a Veris job. The deployment only accepts queued jobs, so this loop is what
stands between a recruiter dropping a file and the form filling in.
"""

import pytest
from veriis.models import JobResponse

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
