"""Independent receipt processing overlaps without exceeding the configured pool."""
import threading
from types import SimpleNamespace

from app.whatsapp_worker import process_batch


def test_whatsapp_batch_reads_next_document_while_another_waits():
    lock = threading.Lock()
    overlap = threading.Event()
    active = 0
    peak = 0
    seen = []
    def process(identifier, **_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(identifier)
            if active == 2:
                overlap.set()
        assert overlap.wait(2), "The second receipt must start before the first completes"
        with lock:
            active -= 1
    settings = SimpleNamespace(extraction_concurrency=2, extraction_workers=1, extraction_max_parallel=2)
    process_batch(list(range(6)), sessions=None, settings=settings, stop=threading.Event(), process=process)
    assert overlap.is_set() and peak == 2 and active == 0
    assert sorted(seen) == list(range(6))


def test_whatsapp_stopped_batch_does_not_start_receipts():
    stop = threading.Event()
    stop.set()
    seen = []
    process_batch([1, 2], sessions=None, settings=SimpleNamespace(), stop=stop,
                  process=lambda identifier, **_: seen.append(identifier))
    assert seen == []
