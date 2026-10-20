"""
Veris OCR, through its job queue.

The deployment has synchronous OCR switched off ("submit the file to /v1/jobs"), so a
scan is submitted as a job and polled until it finishes. A resume takes 15–20 seconds
on an idle queue; the deadline leaves room for a busy one without holding the request
open indefinitely.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal, Protocol

from veriis import FileDescriptor, VerisOCR
from veriis.models import JobResponse, PassportResponse, ResumeResponse

DEADLINE_SECONDS = 150.0
POLL_SECONDS = 1.5


class ScanFailed(Exception):
    """Veris ran the job and could not read the file."""


class ScanTimedOut(Exception):
    """The job was still queued or running at the deadline."""


class _Jobs(Protocol):
    def get(self, job_id: str) -> JobResponse: ...


def wait_for_job(
    jobs: _Jobs,
    job_id: str,
    *,
    deadline: float = DEADLINE_SECONDS,
    interval: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> JobResponse:
    give_up_at = clock() + deadline
    while True:
        job = jobs.get(job_id)
        if job.status == "succeeded":
            return job
        if job.status == "failed":
            raise ScanFailed(job.error.message if job.error else "The OCR job failed.")
        remaining = give_up_at - clock()
        if remaining <= 0:
            raise ScanTimedOut(job_id)
        sleep(min(interval, remaining))


def scan(
    kind: Literal["resume", "passport"],
    doc: FileDescriptor,
    *,
    base_url: str,
    api_key: str,
) -> ResumeResponse | PassportResponse:
    with VerisOCR(base_url=base_url, api_key=api_key) as client:
        accepted = client.jobs.submit(doc, mode=kind)
        job = wait_for_job(client.jobs, accepted.job_id)

    model = ResumeResponse if kind == "resume" else PassportResponse
    return model.model_validate(job.result or {})
