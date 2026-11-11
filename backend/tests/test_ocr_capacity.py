"""Provider capacity must release a worker without losing request identity."""
import pytest
from veriis.errors import VerisOCRRateLimitError, VerisOCRServerError

from app.integrations.ocr import client


@pytest.mark.parametrize("error, expected", [
    (VerisOCRServerError("private provider detail", status=503, code="job_queue_full"), 30),
    (VerisOCRRateLimitError("private provider detail", status=429, retry_after=45), 45),
    (VerisOCRServerError("private provider detail", status=503, code="job_queue_full", retry_after=0), 1),
    (VerisOCRRateLimitError("private provider detail", retry_after=float("nan")), 30),
    (VerisOCRRateLimitError("private provider detail", retry_after=float("inf")), 30),
    (VerisOCRRateLimitError("private provider detail", retry_after=-1), 30),
    (VerisOCRRateLimitError("private provider detail", retry_after=True), 30),
    (VerisOCRRateLimitError("private provider detail", retry_after=90000), 3600),
    (VerisOCRServerError("private provider detail", status=503, code="provider_down"), None),
    (VerisOCRServerError("private provider detail", status=500, code="job_queue_full"), None),
    (ValueError("private document content"), None),
])
def test_capacity_requires_an_explicit_provider_signal(error, expected):
    assert client.capacity_retry_after(error) == expected


@pytest.mark.parametrize("error", [
    VerisOCRRateLimitError("private detail", status=429, retry_after=60),
    VerisOCRServerError("private detail", status=503, code="job_queue_full"),
])
def test_durable_worker_does_not_repeat_upload_while_provider_is_full(monkeypatch, error):
    calls = []

    def submit():
        calls.append("same-idempotency-key")
        raise error

    monkeypatch.setattr(client, "_sleep", lambda _: pytest.fail("A durable worker must release its slot"))
    with pytest.raises(type(error)) as captured:
        client.retrying(submit, defer_capacity=True)
    assert captured.value is error
    assert calls == ["same-idempotency-key"]


def test_unrelated_server_fault_still_uses_bounded_retry(monkeypatch):
    calls, sleeps = [], []

    def submit():
        calls.append("same-idempotency-key")
        if len(calls) == 1:
            raise VerisOCRServerError("private detail", status=502)
        return {"accepted": True}

    monkeypatch.setattr(client, "_sleep", sleeps.append)
    assert client.retrying(submit, defer_capacity=True) == {"accepted": True}
    assert calls == ["same-idempotency-key", "same-idempotency-key"]
    assert sleeps == [0.5]


def test_synchronous_retry_contract_is_unchanged(monkeypatch):
    calls, sleeps = [], []

    def submit():
        calls.append("same-idempotency-key")
        if len(calls) == 1:
            raise VerisOCRRateLimitError("private detail", status=429, retry_after=2)
        return {"accepted": True}

    monkeypatch.setattr(client, "_sleep", sleeps.append)
    assert client.retrying(submit) == {"accepted": True}
    assert len(calls) == 2 and sleeps == [2]
