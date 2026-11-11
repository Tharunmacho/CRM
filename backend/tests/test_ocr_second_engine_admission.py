"""A slow second opinion cannot build an unbounded queue of retained page images."""
from concurrent.futures import Future
import threading
import time
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from app.integrations.ocr import second_opinion as subject


@pytest.fixture
def isolated_pool(monkeypatch):
    monkeypatch.setattr(subject, "_POOL", None)
    monkeypatch.setattr(subject, "_ADMISSION", None)
    monkeypatch.setattr(subject, "_POOL_SIZE", 0)
    monkeypatch.setattr(subject, "available", lambda: True)
    monkeypatch.setattr(subject, "MIN_SECONDS_LEFT", 0.001)
    monkeypatch.setattr(subject, "ADMISSION_WAIT_SECONDS", 0.005)
    monkeypatch.setenv("OCR_SECOND_ENGINE_SLOTS", "1")
    yield
    if subject._POOL is not None and hasattr(subject._POOL, "shutdown"):
        subject._POOL.shutdown(wait=True, cancel_futures=True)


def test_timed_out_running_work_keeps_its_slot_and_new_pages_are_not_enqueued(isolated_pool, monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_run(pixels):
        calls.append(pixels.shape)
        started.set()
        assert release.wait(3)
        return []

    monkeypatch.setattr(subject, "_run", slow_run)
    try:
        with Image.new("RGB", (32, 32), "white") as image:
            first = subject.read_image(image, deadline=time.monotonic() + 0.15)
            assert started.is_set() and "ran out of time" in first.error
            for _ in range(20):
                waiting = subject.read_image(image, deadline=time.monotonic() + 0.1)
                assert waiting.deferred and "busy" in waiting.error
            assert len(calls) == 1
            assert not subject._ADMISSION.acquire(blocking=False)
    finally:
        release.set()
    subject._POOL.shutdown(wait=True)
    assert subject._ADMISSION.acquire(blocking=False)
    subject._ADMISSION.release()


def test_pending_future_is_cancelled_at_deadline_and_releases_admission(isolated_pool, monkeypatch):
    pending = Future()

    class DelayedExecutor:
        def submit(self, *args):
            return pending

    monkeypatch.setattr(subject, "_POOL", DelayedExecutor())
    monkeypatch.setattr(subject, "_ADMISSION", threading.BoundedSemaphore(1))
    with Image.new("RGB", (32, 32), "white") as image:
        result = subject.read_image(image, deadline=time.monotonic() + 0.03)
    assert "ran out of time" in result.error
    assert pending.cancelled()
    assert subject._ADMISSION.acquire(blocking=False)
    subject._ADMISSION.release()


def test_submission_failure_releases_the_slot_for_the_next_document(isolated_pool, monkeypatch):
    class BrokenExecutor:
        def submit(self, *args):
            raise RuntimeError("private image details must not be reported")

    monkeypatch.setattr(subject, "_POOL", BrokenExecutor())
    monkeypatch.setattr(subject, "_ADMISSION", threading.BoundedSemaphore(1))
    with Image.new("RGB", (32, 32), "white") as image:
        result = subject.read_image(image, deadline=time.monotonic() + 1)
    assert result.error == "The second reading engine failed (RuntimeError)."
    assert subject._ADMISSION.acquire(blocking=False)
    subject._ADMISSION.release()


def test_pixel_preparation_and_slot_wait_share_the_callers_deadline(isolated_pool, monkeypatch):
    now, timeouts = [100.0], []

    class CompletedFuture:
        def add_done_callback(self, callback):
            callback(self)

        def result(self, timeout):
            timeouts.append(timeout)
            return []

    class PreparingExecutor:
        def submit(self, *args):
            now[0] += 3
            return CompletedFuture()

    monkeypatch.setattr(subject.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(subject, "_POOL", PreparingExecutor())
    monkeypatch.setattr(subject, "_ADMISSION", threading.BoundedSemaphore(1))
    with Image.new("RGB", (32, 32), "white") as image:
        result = subject.read_image(image, deadline=110.0)
    assert not result.error
    assert timeouts == [7.0]


def test_model_initialization_does_not_lock_out_pool_admission(isolated_pool, monkeypatch):
    loading, release, pool_ready = threading.Event(), threading.Event(), threading.Event()

    def build_engine(**kwargs):
        loading.set()
        release.wait(3)
        return object()

    monkeypatch.setattr(subject, "_ENGINE", None)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=build_engine))
    loader = threading.Thread(target=subject._engine)
    loader.start()
    checker = threading.Thread(target=lambda: (subject._pool(), pool_ready.set()))
    try:
        assert loading.wait(1)
        checker.start()
        assert pool_ready.wait(0.5), "Model loading must not block admission and its caller deadline"
    finally:
        release.set()
        loader.join(3)
        if checker.ident:
            checker.join(3)
