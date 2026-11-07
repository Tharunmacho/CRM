"""A second local reading engine (RapidOCR on ONNX Runtime) for pages Tesseract reads badly.

It is only a second opinion: ``inspection`` calls it for pages whose first Tesseract pass is weak
(photographed cards, textured passport pages, low contrast scans) and merges its lines with
Tesseract's, line by line, keeping the better read of every region. Nothing leaves the machine: the
models ship inside the wheel and no network is used.

* Safe import: when the package or its models are missing, or ``OCR_SECOND_ENGINE=off``, every
  function reports "not available" and the pipeline carries on with Tesseract alone.
* One shared engine object, created lazily; the number of simultaneous runs is bounded by ``OCR_SECOND_ENGINE_SLOTS``
  (default: half the CPUs, at least one) so the extraction queue cannot oversubscribe the machine.
* Bounded time: a run starts only when the caller's deadline leaves room for it, and the ONNX
  threads per run are limited. A run that cannot be interrupted is still bounded by the image size.
"""
from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field

from .read_merge import Line

_STATE_LOCK = threading.Lock()
_STATE: dict = {"checked": False, "ok": False}
_POOL: ThreadPoolExecutor | None = None
_POOL_SIZE = 0
# Seconds a single run is expected to need at most on a normal page; a run is not started with less left.
MIN_SECONDS_LEFT = 2.0
# The most one page waits for this engine (a normal page needs 3-8 s, a busy machine more).
RUN_SECONDS = float(os.environ.get("OCR_SECOND_ENGINE_SECONDS", "40") or 40)


@dataclass
class SecondRead:
    lines: list[Line] = field(default_factory=list, repr=False)
    confidence: float = 0.0   # 0-100, mean over words
    words: int = 0
    seconds: float = 0.0
    error: str = ""


def _enabled() -> bool:
    return os.environ.get("OCR_SECOND_ENGINE", "on").strip().lower() not in {"off", "0", "false", "no"}


def available() -> bool:
    """Whether the second engine can be used (import checked once, never raises)."""
    if not _enabled():
        return False
    with _STATE_LOCK:
        if not _STATE["checked"]:
            _STATE["checked"] = True
            try:
                import numpy  # noqa: F401
                from rapidocr_onnxruntime import RapidOCR  # noqa: F401
                _STATE["ok"] = True
            except Exception:  # not installed, or its native libraries are unusable here
                _STATE["ok"] = False
    return bool(_STATE["ok"])


def _pool() -> ThreadPoolExecutor:
    """The worker threads that run the engine; their number is the limit of simultaneous runs."""
    global _POOL, _POOL_SIZE
    with _STATE_LOCK:
        if _POOL is None:
            try:
                limit = int(os.environ.get("OCR_SECOND_ENGINE_SLOTS", "0"))
            except ValueError:
                limit = 0
            _POOL_SIZE = limit if limit > 0 else max(1, (os.cpu_count() or 2) // 2)
            _POOL = ThreadPoolExecutor(max_workers=_POOL_SIZE, thread_name_prefix="crm-ocr2")
        return _POOL


def set_slots(limit: int) -> None:
    """Replace the run limit (tests and deployments with other needs)."""
    global _POOL, _POOL_SIZE
    with _STATE_LOCK:
        _POOL_SIZE = max(1, limit)
        _POOL = ThreadPoolExecutor(max_workers=_POOL_SIZE, thread_name_prefix="crm-ocr2")


def slot_count() -> int:
    _pool()
    return _POOL_SIZE


_ENGINE = None


def _engine():
    """One engine for the process: the ONNX sessions are safe to call from several threads and the
    wrapper holds no per-call state, so sharing it costs one copy of the models, not one per thread."""
    global _ENGINE
    with _STATE_LOCK:
        if _ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR

            threads = max(1, int(os.environ.get("OCR_SECOND_ENGINE_THREADS", "2") or 2))
            side = int(os.environ.get("OCR_SECOND_ENGINE_MAX_SIDE", "1600") or 1600)
            _ENGINE = RapidOCR(intra_op_num_threads=threads, inter_op_num_threads=1, text_score=0.3, max_side_len=side)
        return _ENGINE


def _run(rgb_bgr):
    result, _ = _engine()(rgb_bgr)
    return result


def read_image(image, *, deadline: float, frame: str = "page") -> SecondRead:
    """Read a Pillow image. Lines carry boxes normalised to the image (0..1) and confidence 0-100.

    The run happens on a pool thread and the caller waits only until ``deadline``; a run that is
    still going then is abandoned (its result is discarded) so the page is never held up by it.
    """
    if not available():
        return SecondRead(error="The second reading engine is not available.")
    left = deadline - time.monotonic()
    if left < MIN_SECONDS_LEFT:
        return SecondRead(error="Not enough time left for the second reading engine.")
    started = time.monotonic()
    try:
        import numpy as np

        width, height = image.size
        rgb = np.asarray(image.convert("RGB"))
        future = _pool().submit(_run, rgb[:, :, ::-1].copy())
        result = future.result(timeout=left)
    except FutureTimeout:
        return SecondRead(error="The second reading engine ran out of time.", seconds=time.monotonic() - started)
    except Exception as exc:  # a failing second opinion must never fail the page
        return SecondRead(error=f"The second reading engine failed ({type(exc).__name__}).")
    lines: list[Line] = []
    scores: list[float] = []
    for item in result or []:
        try:
            points, text, score = item[0], str(item[1]).strip(), float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if not text:
            continue
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        box = (max(0.0, min(xs) / width), max(0.0, min(ys) / height), min(1.0, max(xs) / width), min(1.0, max(ys) / height))
        count = max(1, len(text.split()))
        lines.append(Line(text, score * 100.0, count, box, frame, engine="rapidocr"))
        scores.extend([score * 100.0] * count)
    return SecondRead(lines, sum(scores) / len(scores) if scores else 0.0, len(scores), time.monotonic() - started)
