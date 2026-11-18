"""Why the online document reader did not give a usable answer: one precise reason per failure, in plain words.

Every failure of a reading is turned into a *reason*: ``{"errorClass", "httpStatus", "retriable", "code", "detail"}``.
``detail`` is safe by construction: field names, status codes, the reader's own short code, lengths. It never holds
document text, a name or an ID number. ``message`` turns a reason into the sentence the screen shows; the caller adds the
attempt it is on and when the next automatic attempt is due.

Transient classes (a timeout, a dropped connection, a 5xx, a busy reader, a reader answer that was cut off) are tried
again automatically with the same idempotency key; permanent ones (the reader refused the file, our key is wrong, the
request was invalid) are not, because trying again cannot change the answer.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

# class -> (retriable, short sentence). ``retriable`` False: a person has to change something first.
CLASSES: dict[str, tuple[bool, str]] = {
    "reader_timeout": (True, "The reader did not answer in time"),
    "reader_unreachable": (True, "We could not reach the document reader"),
    "reader_bad_data": (True, "The reader answered, but its reply was cut off or was not valid data"),
    "reader_busy": (True, "The reader is busy (too many requests)"),
    "reader_slow": (True, "The reader is taking longer than usual"),
    "reader_server_error": (True, "The reader had a fault on its side"),
    "reader_key_conflict": (True, "The reader already had this upload key for a different file"),
    "reader_result_expired": (True, "The reader no longer holds the result of an earlier reading of these pages (it expired), so they are sent again"),
    "reader_job_failed": (True, "The reader could not read the file"),
    "reader_answer_unusable": (True, "The reader answered, but we could not use the answer"),
    "reader_no_result": (True, "The reader finished but sent no result"),
    "reader_rejected_file": (False, "The reader refused this file"),
    "reader_auth": (False, "The reader did not accept our access key"),
    "reader_not_found": (False, "The reader no longer has this reading job"),
    "reader_rejected_request": (False, "The reader rejected the request as invalid"),
    "reader_missing_field": (False, "The reader answered, but a field was missing"),
    "reader_not_set_up": (False, "Document reading is not set up on this server"),
    "internal_error": (True, "Reading stopped because of a fault on our side"),
}

# What a person can do about a class that will not fix itself.
_ADVICE = {
    "reader_rejected_file": "Upload a smaller or clearer copy of the file.",
    "reader_auth": "An administrator must check the reader access key on the server.",
    "reader_not_found": "Ask for the document to be read again.",
    "reader_rejected_request": "Tell the developer; send the diagnostics report.",
    "reader_not_set_up": "An administrator must finish the reader setup on the server.",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9_.\[\]:,;()/ -]")


def safe_text(value: Any, limit: int = 160) -> str:
    """One short line with no control characters and nothing but plain symbols (never a quote or a URL query)."""
    return " ".join(_UNSAFE.sub("", str(value or "")).split())[:limit]


def validation_detail(error: ValidationError, limit: int = 200) -> str:
    """The fields a pydantic check failed on ("missing: pages[0].text; invalid: processing_time_ms (int_parsing)").
    Only locations and the rule's name are used: the offending value (which could be document text) never is."""
    missing, invalid = [], []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        where = ".".join(str(part) for part in item.get("loc", ())).replace(".[", "[") or "(whole answer)"
        where = re.sub(r"\.(\d+)", r"[\1]", where)
        if item.get("type") == "missing":
            missing.append(where)
        else:
            invalid.append(f"{where} ({item.get('type', 'invalid')})")
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(dict.fromkeys(missing[:6])))
    if invalid:
        parts.append("invalid: " + ", ".join(dict.fromkeys(invalid[:6])))
    return safe_text("; ".join(parts) or "the answer did not match the expected shape", limit)


def _make(error_class: str, *, http_status: int | None = None, code: str | None = None, detail: str = "",
          retriable: bool | None = None) -> dict:
    reason = {"errorClass": error_class, "retriable": CLASSES[error_class][0] if retriable is None else bool(retriable),
              "detail": safe_text(detail)}
    if http_status:
        reason["httpStatus"] = int(http_status)
    if code:
        reason["code"] = safe_text(code, 60)
    return reason


def classify(error: BaseException) -> dict:
    """The reason for an exception raised while talking to the reader (SDK error, malformed answer, unexpected fault)."""
    from veriis.errors import (VerisOCRAuthenticationError, VerisOCRBadRequestError, VerisOCRConnectionError,
                               VerisOCRNotFoundError, VerisOCRRateLimitError, VerisOCRServerError, VerisOCRTimeoutError,
                               VerisOCRValidationError)
    status = getattr(error, "status", None)
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    code = getattr(error, "code", None)
    code = code if isinstance(code, str) else None
    if isinstance(error, ValidationError):
        return _make("reader_answer_unusable", detail=validation_detail(error))
    if isinstance(error, VerisOCRTimeoutError):
        return _make("reader_timeout", detail="no answer within 30 seconds")
    if isinstance(error, VerisOCRConnectionError):
        if status is not None:  # the reader did answer, but what came back was not parseable
            return _make("reader_bad_data", http_status=status, detail="the reply was not valid JSON")
        return _make("reader_unreachable", detail="the connection failed or was dropped")
    if isinstance(error, VerisOCRRateLimitError) or status == 429 or (status == 503 and code == "job_queue_full"):
        hint = getattr(error, "retry_after", None)
        extra = f"the reader asked us to wait {int(hint)} seconds" if isinstance(hint, (int, float)) and not isinstance(hint, bool) and hint >= 0 else "the reader's queue is full"
        return _make("reader_busy", http_status=status or 429, code=code, detail=extra)
    if isinstance(error, VerisOCRServerError) or (status is not None and status >= 500):
        return _make("reader_server_error", http_status=status, code=code, detail=f"HTTP {status}" if status else "")
    if status == 410 or code == "result_expired":
        return _make("reader_result_expired", http_status=410, code=code or "result_expired", detail="the saved result of the earlier job is gone")
    if status == 409:
        return _make("reader_key_conflict", http_status=409, code=code, detail="the same key was used for other file contents")
    if isinstance(error, VerisOCRAuthenticationError) or status in (401, 403):
        return _make("reader_auth", http_status=status, code=code, detail=f"HTTP {status}")
    if isinstance(error, VerisOCRBadRequestError) or status in (400, 413):
        return _make("reader_rejected_file", http_status=status, code=code,
                     detail="the file is too large" if status == 413 else "the file or request was not accepted")
    if isinstance(error, VerisOCRNotFoundError) or status == 404:
        return _make("reader_not_found", http_status=404, code=code, detail="the job is unknown to the reader")
    if isinstance(error, VerisOCRValidationError) or status == 422:
        fields = _locations(getattr(error, "detail", None))
        return _make("reader_rejected_request", http_status=422, code=code, detail=("invalid: " + fields) if fields else "HTTP 422")
    if status is not None:
        return _make("reader_rejected_request", http_status=status, code=code, detail=f"HTTP {status}")
    return _make("internal_error", detail=type(error).__name__)


def _locations(detail: Any) -> str:
    """Field locations of an API validation error body ("body.file, body.mode"); the messages (which may echo input) are dropped."""
    found = []
    if isinstance(detail, list):
        for item in detail[:6]:
            loc = item.get("loc") if isinstance(item, dict) else None
            if isinstance(loc, (list, tuple)) and loc:
                found.append(".".join(str(part) for part in loc))
    return safe_text(", ".join(dict.fromkeys(found)), 120)


def from_job(*, status: str | None, code: str | None = None, message: str | None = None, retryable: bool | None = None,
             result_is_dict: bool = True) -> dict:
    """The reason for a reader job that ended without a usable result (it was ``failed``, or ``succeeded`` with nothing)."""
    if status == "failed":
        detail = " - ".join(part for part in (f"reader code {safe_text(code, 60)}" if code else "", safe_text(message, 120)) if part)
        return _make("reader_job_failed", code=code, detail=detail or "the reader gave no reason",
                     retriable=True if retryable is None else retryable)
    if status == "succeeded" and not result_is_dict:
        return _make("reader_no_result", detail="the job succeeded but its result was not an object")
    return _make("reader_no_result", detail=f"job status '{safe_text(status, 30)}' carried no result")


def unusable(error: ValidationError | None = None, detail: str = "") -> dict:
    """A reader answer that does not match what we expect (``error``: the pydantic check that failed)."""
    return _make("reader_answer_unusable", detail=validation_detail(error) if error is not None else detail)


def missing_field(label: str, *, kind: str = "passport") -> dict:
    return _make("reader_missing_field", detail=f"the {safe_text(kind, 20)} answer had no {safe_text(label, 40)}")


def custom(error_class: str, detail: str = "", **extra) -> dict:
    return _make(error_class, detail=detail, **extra)


def _wait_phrase(seconds: float) -> str:
    if seconds < 90:
        return "in about a minute"
    return f"in about {round(seconds / 60)} minutes"


def message(reason: dict, *, attempt: int | None = None, max_attempts: int | None = None,
            retry_in: float | None = None, subject: str = "") -> str:
    """The sentence the screen shows for a reason (``subject``: e.g. "passport", for a missing-field reason)."""
    error_class = reason.get("errorClass") or "internal_error"
    _retriable, sentence = CLASSES.get(error_class, CLASSES["internal_error"])
    status = reason.get("httpStatus")
    detail = reason.get("detail") or ""
    if error_class == "reader_missing_field":
        sentence = f"The reader answered, but the {detail.rsplit(' had no ', 1)[-1] if ' had no ' in detail else 'requested'} field was missing"
        detail = ""
    elif error_class == "reader_job_failed" and reason.get("code"):
        detail = detail  # already holds the reader's code and its own sentence
    paren = ", ".join(part for part in (f"HTTP {status}" if status and f"HTTP {status}" not in detail else "", detail) if part)
    text = f"{sentence}" + (f" ({paren})" if paren else "") + "."
    retriable = bool(reason.get("retriable"))
    if attempt and max_attempts:
        if retriable and attempt < max_attempts:
            text = text[:-1] + f" (attempt {attempt} of {max_attempts}). " + (
                f"It will be tried again {_wait_phrase(retry_in)}." if retry_in else "It will be tried again soon.")
        elif retriable:
            text = text[:-1] + f" (attempt {attempt} of {max_attempts}). The automatic attempts are used up; ask for the document to be read again later."
        else:
            text += " " + _ADVICE.get(error_class, "This will not get better by trying again.")
    elif not retriable and error_class in _ADVICE:
        text += " " + _ADVICE[error_class]
    return text


def is_permanent(reason: dict | None) -> bool:
    return bool(reason) and reason.get("retriable") is False and reason.get("errorClass") != "reader_missing_field"
