"""
Veris OCR, through its job queue.

Submit selected document pages as a job and poll for a bounded time. Retries
use a stable idempotency key; a slow job never falls through to a second,
synchronous extraction endpoint.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal, Protocol

from veriis import FileDescriptor, VerisOCR
from veriis.models import AadhaarResponse, JobResponse, PassportResponse, ResumeResponse

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
            raise ScanFailed(job.error.message if job.error else "The document reader could not read this file.")
        remaining = give_up_at - clock()
        if remaining <= 0:
            raise ScanTimedOut(job_id)
        sleep(min(interval, remaining))


def scan(
    kind: Literal["resume", "passport", "aadhaar"],
    doc: FileDescriptor,
    *,
    base_url: str,
    api_key: str,
    idempotency_key: str | None = None,
) -> dict:
    """The job's ``result`` exactly as Veris returned it, checked against the SDK model
    (a malformed payload raises ValidationError) but never rebuilt from it."""
    # The SDK otherwise permits 120 seconds per request plus automatic retries,
    # which could exceed the polling budget by several minutes.
    with VerisOCR(base_url=base_url, api_key=api_key, timeout=30.0, max_retries=0) as client:
        accepted = client.jobs.submit(doc, mode=kind, idempotency_key=idempotency_key)
        job = wait_for_job(client.jobs, accepted.job_id)

    model = {"resume": ResumeResponse, "passport": PassportResponse, "aadhaar": AadhaarResponse}[kind]
    payload = job.result or {}
    model.model_validate(payload)
    return payload
